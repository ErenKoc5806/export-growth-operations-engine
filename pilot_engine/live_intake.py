"""Authorized local intake only: no outbound/provider/legacy workflow adapters."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from pilot_engine.access import LocalAccess
from pilot_engine.live_migration import _connect, _live_version, _private


FIELDS = {
    "PRODUCT_PROFILE": {"sku": "Ürün kodu", "product_name": "Ürün adı",
        "dimensions": "Çizim / ölçüler", "material": "Malzeme", "intended_use": "Kullanım amacı",
        "hs6": "İncelenmiş HS6", "classification_ref": "Sınıflandırma inceleme referansı"},
    "PRICE_AUTHORITY": {"sku": "Ürün kodu", "unit_price": "Birim fiyat", "currency": "Para birimi",
        "unit": "Birim", "payment_terms": "Ödeme koşulları", "incoterm": "Incoterm ve yer",
        "tax_authority_ref": "Vergi / fiyat yetkisi referansı", "valid_until_utc": "Fiyat geçerlilik sonu"},
    "BUYER_SOURCE": {"company": "Alıcı şirket", "country": "Ülke kodu", "website": "Kaynak web adresi",
        "role": "Alıcının rolü", "fit_evidence": "Ürün ve alıcı uygunluğu kanıtı"},
    "CUSTOMER_PO": {"po_number": "Müşteri sipariş numarası", "buyer": "Alıcı şirket",
        "sku": "Ürün kodu", "quantity": "Miktar", "unit": "Birim", "unit_price": "Birim fiyat",
        "currency": "Para birimi", "total": "Toplam", "payment_terms": "Ödeme koşulları",
        "incoterm": "Incoterm ve yer", "delivery": "Talep edilen teslimat"},
}
KINDS = {"PRODUCT_PROFILE": "Ürün ve sınıflandırma", "PRICE_AUTHORITY": "Fiyat ve vergi yetkisi",
         "BUYER_SOURCE": "Alıcı kaynağı", "CUSTOMER_PO": "Müşteri PO"}


def _now():
    return datetime.now(timezone.utc)


def _stamp():
    return _now().isoformat(timespec="microseconds")


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value):
    return hashlib.sha256(value).hexdigest()


def _text(value, label):
    if not isinstance(value, str) or not value.strip() or len(value) > 4000:
        raise ValueError(label + ": dolu bir alan gerekli (en çok 4000 karakter)")
    value = value.strip()
    if value.upper().startswith("SYN-"):
        raise ValueError("Sentetik kanıt canlı kaydı yetkilendiremez")
    return value


def _time(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError()
        return result.astimezone(timezone.utc)
    except (ValueError, AttributeError, OverflowError) as exc:
        raise ValueError("Saat dilimi içeren geçerli tarih/saat gerekli") from exc


def _future(value):
    result = _time(value)
    if result <= _now():
        raise ValueError("Geçerlilik / saklama süresi dolmuş")
    return result.isoformat()


def _amount(value):
    if not re.fullmatch(r"[0-9]{1,12}(?:\.[0-9]{1,6})?", value):
        raise ValueError("Tutar / miktar: en çok 12 tam ve 6 ondalık basamak, nokta ayırıcı kullanın")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("Pozitif ve sonlu tutar / miktar gerekli") from exc
    if not result.is_finite() or result <= 0 or len(value) > 30:
        raise ValueError("Pozitif ve sonlu tutar / miktar gerekli")
    return result


class LiveIntake:
    def __init__(self, database, access=None):
        self.path = Path(database).absolute()
        self.access = access if access is not None else LocalAccess.single_operator()
        self.actor = self.require("READ_LIVE_INTAKE")
        with self.transaction(write=False) as db:
            if _live_version(db) != 2:
                raise ValueError("Önce yedekli canlı şema v2 bakımını çalıştırın")

    def require(self, permission):
        actor, allowed = self.access.decision(permission)
        if not allowed or (hasattr(self, "actor") and actor != self.actor):
            raise PermissionError("Canlı kaynak erişimi reddedildi")
        return actor

    @contextmanager
    def transaction(self, *, write=True):
        self.require("RECORD_LIVE_INTAKE" if write else "READ_LIVE_INTAKE")
        _private(self.path.parent, directory=True)
        _private(self.path)
        for suffix in ("-wal", "-shm", "-journal"):
            sidecar = Path(str(self.path) + suffix)
            if sidecar.exists() or sidecar.is_symlink():
                _private(sidecar)
        with closing(_connect(self.path)) as db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def document(self, db, content):
        if not isinstance(content, bytes) or not 1 <= len(content) <= 5_000_000:
            raise ValueError("Dolu bir kanıt dosyası gerekli (en çok 5 MB)")
        digest = _hash(content)
        row = db.execute("SELECT id FROM live_document WHERE sha256=?", (digest,)).fetchone()
        if row:
            return row[0]
        identity = "LIVE-DOC-" + uuid4().hex
        db.execute("INSERT INTO live_document VALUES (?,?,?,?,?)",
                   (identity, digest, content, self.actor, _stamp()))
        return identity

    def enroll(self, values, manufacturer_document, it_document):
        """Record supplied #13 evidence; never infer consent from a config flag."""
        keys = ("manufacturer_name", "authorization_ref", "operator_name", "approver_name",
                "approver_role", "authorizer_name", "it_reviewer", "baseline", "backup_owner")
        data = {key: _text(values.get(key), key) for key in keys}
        if data["operator_name"].casefold() == data["approver_name"].casefold():
            raise ValueError("Operatör ve onaylayan ayrı kişiler olmalı")
        if any(values.get(key) != "yes" for key in
               ("manufacturer_accepted", "it_accepted", "trust_accepted", "permission_accepted")):
            raise ValueError("Üretici / IT kabulü ve veri işleme izni kanıtları gerekli")
        data["expires_at_utc"] = _future(values.get("expires_at_utc"))
        data.update(hs6="732690", country="DE", currency="EUR")
        with self.transaction() as db:
            if db.execute("SELECT 1 FROM live_pilot").fetchone():
                raise ValueError("Bu ilk pilot tek üretici içindir; kayıt zaten var")
            mdoc = self.document(db, manufacturer_document)
            idoc = self.document(db, it_document)
            identity = "LIVE-PILOT-" + uuid4().hex
            db.execute("INSERT INTO live_pilot VALUES (?,?,?,?,?,?,?,?)",
                       (identity, data["manufacturer_name"], data["authorization_ref"],
                        data["operator_name"], data["approver_name"], "LIVE", self.actor, _stamp()))
            db.execute("INSERT INTO live_authorization VALUES (?,?,?,?,?,?,?,?)",
                       (identity, mdoc, idoc, self.actor, data["approver_role"],
                        data["expires_at_utc"], _json(data), "OPERATOR_ATTESTATION"))
            return identity

    def pilot(self, db):
        rows = db.execute("SELECT p.*, a.operator_actor, a.approver_role, a.expires_at_utc, "
                          "a.scope_json FROM live_pilot p JOIN live_authorization a ON a.pilot_id=p.id").fetchall()
        if len(rows) > 1:
            raise ValueError("Bu sürüm tek yetkili üretici destekler")
        return dict(rows[0]) if rows else None

    def gate(self, db):
        pilot = self.pilot(db)
        if not pilot:
            raise ValueError("Önce #13 üretici ve IT yetkilendirme kanıtlarını kaydedin")
        if pilot["operator_actor"] != self.actor:
            raise PermissionError("Kayıtlı operatör OS hesabı gerekli")
        _future(pilot["expires_at_utc"])
        for row in db.execute("SELECT manufacturer_document_id, it_document_id FROM live_authorization WHERE pilot_id=?",
                              (pilot["id"],)):
            self.read_document(db, row[0])
            self.read_document(db, row[1])
        if db.execute("SELECT 1 FROM live_intake_stop WHERE pilot_id=? AND source_id IS NULL",
                      (pilot["id"],)).fetchone():
            raise ValueError("Pilot kaynak kabulü durdurulmuş")
        return pilot

    def validate(self, kind, values):
        if kind not in FIELDS:
            raise ValueError("Kaynak türünü seçin")
        data = {key: _text(values.get(key), label) for key, label in FIELDS[kind].items()}
        if "hs6" in data and data["hs6"] != "732690":
            raise ValueError("Pilot kapsamı HS6 732690")
        if "currency" in data and data["currency"] != "EUR":
            raise ValueError("Pilot para birimi EUR")
        if "country" in data and data["country"] != "DE":
            raise ValueError("Pilot hedef ülkesi DE")
        for key in ("unit_price", "quantity", "total"):
            if key in data:
                _amount(data[key])
        if "total" in data:
            with localcontext() as precision:
                precision.prec = 50
                if _amount(data["quantity"]) * _amount(data["unit_price"]) != _amount(data["total"]):
                    raise ValueError("PO miktar × birim fiyat toplamla eşleşmeli")
        if "valid_until_utc" in data:
            data["valid_until_utc"] = _future(data["valid_until_utc"])
        if "website" in data:
            parsed = urlsplit(data["website"])
            host = (parsed.hostname or "").lower()
            if (parsed.scheme not in ("http", "https") or not host or parsed.username
                    or any(host == name or host.endswith("." + name) for name in
                           ("example.org", "example.com", "example.net", "invalid", "test", "example"))):
                raise ValueError("Sentetik alan adı yerine yetkili gerçek kaynak adresi gerekli")
        return data

    def save(self, kind, values, source_document, permission_document, *, intake_id=None,
             expected_revision=0, expected_hash=""):
        data = self.validate(kind, values)
        provenance = {key: _text(values.get(key), key) for key in
                      ("source_ref", "permission_ref", "terms_ref", "processing_basis", "reviewer_name")}
        provenance["retention_until_utc"] = _future(values.get("retention_until_utc"))
        with self.transaction() as db:
            pilot = self.gate(db)
            if provenance["reviewer_name"] != pilot["approver_name"]:
                raise ValueError("Kaynak izni kayıtlı yetkili tarafından incelenmeli")
            if intake_id:
                prior = self.current(db, intake_id)
                if (prior["revision"], prior["payload_sha256"]) != (expected_revision, expected_hash):
                    raise ValueError("Kayıt sürümü değişti; yeniden inceleyin")
                if prior["kind"] != kind or prior["pilot_id"] != pilot["id"]:
                    raise ValueError("Kaynak türü / pilot eşleşmiyor")
            else:
                if expected_revision != 0 or expected_hash:
                    raise ValueError("Yeni kayıt sürümü geçersiz")
                intake_id = "LIVE-INTAKE-" + uuid4().hex
                db.execute("INSERT INTO live_intake VALUES (?,?,?)", (intake_id, pilot["id"], kind))
            doc = self.document(db, source_document)
            permission_doc = self.document(db, permission_document)
            source_id = "LIVE-SOURCE-" + uuid4().hex
            db.execute("INSERT INTO live_source VALUES (?,?,?,?,?,?,?,?,?)",
                       (source_id, pilot["id"], kind, provenance["source_ref"], _hash(source_document),
                        len(source_document), "LIVE", self.actor, _stamp()))
            db.execute("INSERT INTO live_source_permission VALUES (?,?,?,?,?,?,?,?,?)",
                       (source_id, doc, permission_doc, provenance["permission_ref"], provenance["terms_ref"],
                        provenance["processing_basis"], provenance["retention_until_utc"],
                        provenance["reviewer_name"], _stamp()))
            payload = {"fields": data, "provenance": provenance, "kind": kind,
                       "source_sha256": _hash(source_document), "permission_document_id": permission_doc}
            encoded = _json(payload)
            db.execute("INSERT INTO live_intake_revision VALUES (?,?,?,?,?,?,?,?,?)",
                       (intake_id, pilot["id"], expected_revision + 1, source_id, encoded,
                        _hash(encoded.encode()), "LIVE", self.actor, _stamp()))
            return intake_id

    def current(self, db, intake_id):
        row = db.execute("SELECT r.*, i.kind FROM live_intake_revision r JOIN live_intake i "
                         "ON i.id=r.intake_id WHERE r.intake_id=? ORDER BY r.revision DESC LIMIT 1",
                         (intake_id,)).fetchone()
        if not row:
            raise ValueError("Kaynak kaydı bulunamadı")
        return dict(row)

    def eligible(self, db, row):
        pilot = self.gate(db)
        if pilot["id"] != row["pilot_id"]:
            raise ValueError("Farklı pilot kaynağı")
        payload = json.loads(row["payload_json"])
        if _hash(row["payload_json"].encode()) != row["payload_sha256"]:
            raise ValueError("Kayıt özeti doğrulanamadı")
        self.validate(row["kind"], payload["fields"])
        _future(payload["provenance"]["retention_until_utc"])
        source = db.execute("SELECT s.*, p.document_id, p.permission_document_id FROM live_source s "
                            "JOIN live_source_permission p ON p.source_id=s.id WHERE s.id=?",
                            (row["source_id"],)).fetchone()
        if (not source or source["pilot_id"] != row["pilot_id"] or source["kind"] != row["kind"]
                or source["data_origin"] != "LIVE" or row["data_origin"] != "LIVE"
                or source["source_sha256"] != payload["source_sha256"]):
            raise ValueError("Kaynak türü / kökeni doğrulanamadı")
        document = self.read_document(db, source["document_id"])
        self.read_document(db, source["permission_document_id"])
        if (document["sha256"] != source["source_sha256"]
                or len(document["content"]) != source["byte_length"]
                or source["permission_document_id"] != payload["permission_document_id"]):
            raise ValueError("Kaynak dosyası / izin kanıtı eşleşmiyor")
        if db.execute("SELECT 1 FROM live_intake_stop WHERE source_id=?", (row["source_id"],)).fetchone():
            raise ValueError("Kaynak izni geri çekilmiş")

    def read_document(self, db, document_id):
        row = db.execute("SELECT * FROM live_document WHERE id=?", (document_id,)).fetchone()
        if not row or _hash(row["content"]) != row["sha256"]:
            raise ValueError("Kanıt dosyası doğrulanamadı")
        return row

    def confirm(self, intake_id, revision, digest, expected_sequence, values, evidence):
        self.require("RECORD_LIVE_CONFIRMATION")
        with self.transaction() as db:
            pilot = self.gate(db)
            row = self.current(db, intake_id)
            if row["pilot_id"] != pilot["id"]:
                raise ValueError("Farklı pilot kaynağı")
            if (row["revision"], row["payload_sha256"]) != (revision, digest):
                raise ValueError("Güncel tam sürüm ve özet gerekli; yeniden inceleyin")
            latest = db.execute("SELECT coalesce(max(sequence),0) FROM live_confirmation WHERE intake_id=?",
                                (intake_id,)).fetchone()[0]
            if latest != expected_sequence:
                raise ValueError("Onay durumu değişti; yeniden inceleyin")
            decision = values.get("decision")
            if decision not in ("APPROVE", "REJECT", "REVOKE"):
                raise ValueError("Geçerli onay kararı gerekli")
            if values.get("approver_name") != pilot["approver_name"] or values.get("reviewed") != "yes":
                raise ValueError("Kayıtlı onaylayanın tam içeriği incelediği teyit edilmeli")
            confirmed = _time(values.get("confirmed_at_utc"))
            if not _time(row["recorded_at_utc"]) <= confirmed <= _now():
                raise ValueError("Teyit zamanı bu sürümün kaydından sonra ve gelecekte olmayan bir zaman olmalı")
            if decision == "APPROVE":
                self.eligible(db, row)
            reason = _text(values.get("reason"), "Karar gerekçesi")
            method = _text(values.get("method"), "Teyit yöntemi")
            evidence_ref = _text(values.get("evidence_ref"), "Teyit kanıt referansı")
            document = self.document(db, evidence)
            cursor = db.execute("INSERT INTO live_confirmation VALUES (NULL,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (intake_id, revision, digest, decision, pilot["approver_name"], pilot["authorization_ref"],
                 method, evidence_ref, reason, confirmed.isoformat(), self.actor, _stamp(), "OPERATOR_ATTESTATION"))
            db.execute("INSERT INTO live_confirmation_evidence VALUES (?,?,?)",
                       (cursor.lastrowid, document, pilot["approver_role"]))

    def stop(self, intake_id, revision, digest, reason):
        with self.transaction() as db:
            pilot = self.gate(db)
            source = None
            if intake_id:
                row = self.current(db, intake_id)
                if row["pilot_id"] != pilot["id"]:
                    raise ValueError("Farklı pilot kaynağı")
                if (row["revision"], row["payload_sha256"]) != (revision, digest):
                    raise ValueError("Güncel sürüm gerekli")
                source = row["source_id"]
            db.execute("INSERT INTO live_intake_stop VALUES (NULL,?,?,?,?,?)",
                       (pilot["id"], source, _text(reason, "Durdurma gerekçesi"), self.actor, _stamp()))

    def view(self, intake_id=None):
        with self.transaction(write=False) as db:
            pilot = self.pilot(db)
            try:
                self.gate(db)
                blocked = ""
            except (ValueError, PermissionError) as exc:
                blocked = str(exc)
            rows = [dict(row) for row in db.execute("SELECT i.id, i.kind, max(r.revision) revision "
                "FROM live_intake i JOIN live_intake_revision r ON r.intake_id=i.id GROUP BY i.id ORDER BY i.rowid DESC")]
            result = {"pilot": pilot, "blocked": blocked, "items": rows}
            if intake_id:
                row = self.current(db, intake_id)
                history = [dict(item) for item in db.execute("SELECT c.*, e.document_id, e.approver_role "
                    "FROM live_confirmation c JOIN live_confirmation_evidence e ON e.confirmation_sequence=c.sequence "
                    "WHERE c.intake_id=? ORDER BY c.sequence DESC", (intake_id,))]
                latest = next((item for item in history if item["revision"] == row["revision"]), None)
                try:
                    self.eligible(db, row)
                    if latest:
                        self.read_document(db, latest["document_id"])
                    status = latest["decision"] if latest else "REVIEW_REQUIRED"
                except (ValueError, PermissionError) as exc:
                    status = "BLOCKED: " + str(exc)
                result.update(row=row, payload=json.loads(row["payload_json"]), history=history,
                              status=status, sequence=history[0]["sequence"] if history else 0)
            return result

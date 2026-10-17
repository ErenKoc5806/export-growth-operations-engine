"""Local live Execute drafts. No provider, synthetic store or issuance adapter."""

from contextlib import closing
from decimal import Decimal, localcontext
from html import escape
import json
from pathlib import Path
import re
import sqlite3
from uuid import uuid4

from pilot_engine.live_intake import (_amount, _future, _hash, _json, _now, _stamp,
                                       _text, _time)
from pilot_engine.live_migration import _connect, _live_version, _private, _sha256


INPUT_KINDS = ("PRODUCT_PROFILE", "PRICE_AUTHORITY", "CUSTOMER_PO")
FACTS = {
    "seller_address": "Satıcı yasal adresi", "seller_tax_id": "Satıcı vergi kimliği / incelenmiş açıklama",
    "buyer_address": "Alıcı yasal adresi", "buyer_tax_id": "Alıcı vergi kimliği / incelenmiş açıklama",
    "ship_to": "Teslim alıcı ve tam adres", "origin_country": "İncelenmiş menşe ülke kodu",
    "destination_country": "Varış ülke kodu", "origin_ref": "Menşe inceleme referansı",
    "classification_ref": "Üreticinin bu ürün için sınıflandırma teyidi",
    "tax_treatment": "Üreticinin vergi uygulaması açıklaması", "tax_ref": "Vergi inceleme referansı",
    "tax_amount": "İncelenmiş vergi tutarı (EUR)", "charges": "İncelenmiş ek masraflar (EUR)",
    "discount": "İncelenmiş indirim (EUR)", "packing_quantity": "Paketlenen ürün miktarı",
    "packages": "Koli / paket adedi", "net_kg": "Toplam net ağırlık (kg)",
    "gross_kg": "Toplam brüt ağırlık (kg)", "package_dimensions": "Paket ölçüleri ve birimi",
    "marks": "Paket işaretleri / incelenmiş yok açıklaması",
    "checklist": "Gerekli / gerekmeyen belgeler, gerekçe, sorumlu ve kalan işler",
    "delivery_review": "PO teslimat talebinin üretici teyidi / kalan işler",
}
PROVENANCE = ("source_ref", "permission_ref", "terms_ref", "processing_basis", "reviewer_name")


def _nonnegative(value):
    if not re.fullmatch(r"[0-9]{1,12}(?:\.[0-9]{1,6})?", value):
        raise ValueError("Nokta ayırıcılı, sonlu ve negatif olmayan tutar gerekli")
    return Decimal(value)


def render_bundle(bundle, digest, review=None):
    """Portable, inert, printable HTML; every document remains visibly unreleased."""
    def h(value):
        return escape(str(value), quote=True)

    body = "<!doctype html><html lang='tr'><meta charset='utf-8'><title>Live Execute DRAFT</title>"
    body += "<body><h1>DRAFT — YAYIMLANMAMIŞ / UNRELEASED</h1>"
    body += "<p>Yerel inceleme taslakları. Hukuki fatura düzenleme veya sevkiyat izni değildir.</p>"
    body += "<p>Tam içerik SHA-256: " + h(digest) + "</p>"
    if review:
        body += "<h2>Tam taslak incelemesi — OPERATÖR BEYANI</h2><pre>"
        body += h(json.dumps(review, ensure_ascii=False, indent=2)) + "</pre>"
    if bundle["requirements"]:
        body += "<h2>İnceleme gereksinimleri</h2><ul>"
        body += "".join("<li>" + h(item) + "</li>" for item in bundle["requirements"]) + "</ul>"
    for title, rows in bundle["documents"].items():
        body += "<section><h2>" + h(title) + " — DRAFT / UNRELEASED</h2><table border='1'>"
        body += "".join("<tr><th>" + h(k) + "</th><td>" + h(v) + "</td></tr>" for k, v in rows.items())
        body += "</table></section>"
    body += "<h2>Kaynaklar, izinler ve tam sürüm onayları</h2>"
    body += "<pre>" + h(json.dumps(bundle["inputs"], ensure_ascii=False, indent=2)) + "</pre>"
    body += "<h2>Sevkiyat bilgilerinin kaynağı ve izni</h2><pre>"
    body += h(json.dumps(bundle["facts_provenance"], ensure_ascii=False, indent=2)) + "</pre>"
    return (body + "</body></html>").encode("utf-8")


class LiveExecute:
    def __init__(self, intake):
        self.intake = intake
        with intake.transaction(write=False) as db:
            if _live_version(db) != 4:
                raise ValueError("Canlı Execute için yedekli şema v4 yükseltmesi gerekli")

    def inputs(self, db, identities):
        pilot = self.intake.gate(db)
        if len(identities) != 3:
            raise ValueError("Ürün, fiyat ve PO kayıtlarını seçin")
        result = []
        for kind, identity in zip(INPUT_KINDS, identities):
            row = self.intake.current(db, identity)
            if row["kind"] != kind:
                raise ValueError("Canlı kaynak türü eşleşmiyor")
            self.intake.eligible(db, row)
            confirmation = db.execute("SELECT c.*, e.document_id, e.approver_role FROM live_confirmation c "
                "JOIN live_confirmation_evidence e ON e.confirmation_sequence=c.sequence "
                "WHERE c.intake_id=? ORDER BY c.sequence DESC LIMIT 1", (identity,)).fetchone()
            if (not confirmation or confirmation["decision"] != "APPROVE"
                    or confirmation["revision"] != row["revision"]
                    or confirmation["payload_sha256"] != row["payload_sha256"]
                    or confirmation["approver_name"] != pilot["approver_name"]
                    or confirmation["approver_role"] != pilot["approver_role"]
                    or confirmation["authority_ref"] != pilot["authorization_ref"]
                    or confirmation["identity_basis"] != "OPERATOR_ATTESTATION"):
                raise ValueError("Her güncel canlı kaynak için tam sürüm insan onayı gerekli")
            self.intake.read_document(db, confirmation["document_id"])
            permission = dict(db.execute("SELECT * FROM live_source_permission WHERE source_id=?",
                                         (row["source_id"],)).fetchone())
            source = dict(db.execute("SELECT * FROM live_source WHERE id=?", (row["source_id"],)).fetchone())
            result.append({"kind": kind, "intake_id": identity, "revision": row["revision"],
                "payload_sha256": row["payload_sha256"], "source_id": row["source_id"],
                "fields": json.loads(row["payload_json"])["fields"], "source": source,
                "permission": permission, "confirmation": dict(confirmation)})
        return result

    def selection(self, identities):
        with self.intake.transaction(write=False) as db:
            inputs = self.inputs(db, identities)
            return inputs, _hash(_json(inputs).encode())

    def bundle(self, pilot, inputs, facts):
        profile, price, po = (item["fields"] for item in inputs)
        requirements = [label + ": bilgi / inceleme eksik" for key, label in FACTS.items() if not facts[key]]
        for key in ("sku", "unit", "currency", "payment_terms", "incoterm"):
            if po[key] != price[key] or (key == "sku" and po[key] != profile[key]):
                requirements.append("PO / yetkili kaynak farkı: " + key + "; yetkili kaynağı düzeltip yeniden onaylayın")
        if _amount(po["unit_price"]) != _amount(price["unit_price"]):
            requirements.append("PO / fiyat yetkisi farkı: unit_price; yeni tam sürüm fiyat onayı gerekli")
        if not re.fullmatch(r"(?:EXW|FCA|CPT|CIP|DAP|DPU|DDP|FAS|FOB|CFR|CIF)\s+\S.+", po["incoterm"]):
            requirements.append("Incoterm kodu ve adlandırılmış yer birlikte gerekli")
        for key, country in (("origin_country", "TR"), ("destination_country", "DE")):
            if facts[key] and facts[key] != country:
                requirements.append(FACTS[key] + ": ilk pilot kapsamı " + country)
        numbers = {}
        for key in ("tax_amount", "charges", "discount", "packing_quantity", "net_kg", "gross_kg"):
            if facts[key]:
                try:
                    numbers[key] = (_nonnegative(facts[key]) if key in ("tax_amount", "charges", "discount")
                                    else _amount(facts[key]))
                except ValueError as exc:
                    requirements.append(FACTS[key] + ": " + str(exc))
        if facts["packages"] and not re.fullmatch(r"[1-9][0-9]{0,8}", facts["packages"]):
            requirements.append("Paket adedi pozitif tam sayı olmalı")
        if "packing_quantity" in numbers and numbers["packing_quantity"] != _amount(po["quantity"]):
            requirements.append("Paketlenen miktar PO miktarıyla eşleşmeli")
        if all(key in numbers for key in ("net_kg", "gross_kg")) and numbers["net_kg"] > numbers["gross_kg"]:
            requirements.append("Brüt ağırlık net ağırlıktan az olamaz")
        total = "İNCELEME GEREKLİ"
        if all(key in numbers for key in ("tax_amount", "charges", "discount")):
            with localcontext() as precision:
                precision.prec = 50
                value = _amount(po["total"]) + numbers["tax_amount"] + numbers["charges"] - numbers["discount"]
                total = format(value, "f")
                if value <= 0:
                    requirements.append("İncelenmiş toplam pozitif olmalı")
        invoice = {"Satıcı": pilot["manufacturer_name"], "Satıcı adresi": facts["seller_address"],
            "Satıcı vergi kimliği": facts["seller_tax_id"], "Alıcı": po["buyer"],
            "Alıcı adresi": facts["buyer_address"], "Alıcı vergi kimliği": facts["buyer_tax_id"],
            "PO": po["po_number"], "Ürün kodu": po["sku"], "Ürün": profile["product_name"],
            "Ürün özellikleri": profile["dimensions"] + " / " + profile["material"] + " / " + profile["intended_use"],
            "HS6": profile["hs6"], "Sınıflandırma yetkisi": profile["classification_ref"],
            "Sınıflandırma teyidi": facts["classification_ref"], "Menşe": facts["origin_country"],
            "Menşe incelemesi": facts["origin_ref"], "Varış": facts["destination_country"],
            "Miktar": po["quantity"], "Birim": po["unit"], "Birim fiyat": po["unit_price"],
            "Mal bedeli": po["total"], "Para birimi": po["currency"], "Ödeme": po["payment_terms"],
            "Incoterm ve yer": po["incoterm"], "Vergi uygulaması": facts["tax_treatment"],
            "Vergi yetkisi": price["tax_authority_ref"] + " / " + facts["tax_ref"],
            "Vergi tutarı": facts["tax_amount"], "Ek masraf": facts["charges"],
            "İndirim": facts["discount"], "İncelenmiş toplam EUR": total}
        packing = {"PO": po["po_number"], "Ürün kodu": po["sku"], "Ürün": profile["product_name"],
            "Teslim alıcı / adres": facts["ship_to"], "Miktar": facts["packing_quantity"], "Birim": po["unit"],
            "Paket adedi": facts["packages"], "Net kg": facts["net_kg"], "Brüt kg": facts["gross_kg"],
            "Paket ölçüleri": facts["package_dimensions"], "İşaretler": facts["marks"]}
        checklist = {"PO": po["po_number"], "Talep edilen teslimat": po["delivery"],
            "Teslimat incelemesi": facts["delivery_review"], "Belge incelemesi": facts["checklist"],
            "Durum": "DRAFT — sevkiyat veya hukuki belge yayım izni verilmemiştir"}
        return {"inputs": inputs, "facts_provenance": {key: facts[key] for key in (*PROVENANCE,
                    "retention_until_utc", "document_id", "permission_document_id")},
                "requirements": requirements, "documents": {"COMMERCIAL_INVOICE_DRAFT": invoice,
                    "PACKING_LIST_DRAFT": packing, "SHIPMENT_DOCUMENT_CHECKLIST_DRAFT": checklist}}

    def save(self, identities, expected_inputs, values, document, permission, *, expected_revision=0,
             expected_hash=""):
        facts = {key: values.get(key) or "" for key in FACTS}
        for key, value in facts.items():
            if value:
                facts[key] = _text(value, FACTS[key])
        facts.update({key: _text(values.get(key), key) for key in PROVENANCE})
        facts["retention_until_utc"] = _future(values.get("retention_until_utc"))
        with self.intake.transaction() as db:
            pilot = self.intake.gate(db)
            inputs = self.inputs(db, identities)
            if _hash(_json(inputs).encode()) != expected_inputs:
                raise ValueError("Seçilen kaynak sürümleri / onayları değişti; yeniden inceleyin")
            if facts["reviewer_name"] != pilot["approver_name"]:
                raise ValueError("Sevkiyat bilgisi izni kayıtlı onaylayan tarafından incelenmeli")
            facts["document_id"] = self.intake.document(db, document)
            facts["permission_document_id"] = self.intake.document(db, permission)
            bundle = self.bundle(pilot, inputs, facts)
            encoded = _json(bundle)
            digest = _hash(encoded.encode())
            order = db.execute("SELECT * FROM live_order WHERE po_intake_id=?", (identities[2],)).fetchone()
            if order:
                row = self.current(db, order["id"])
                if row["bundle_sha256"] == digest and row["facts_json"] == _json(facts):
                    return order["id"]  # Same submission, including after a lost redirect/restart.
                if (row["revision"], row["bundle_sha256"]) != (expected_revision, expected_hash):
                    raise ValueError("Sipariş sürümü değişti; güncel taslağı açıp yeniden inceleyin")
                identity = order["id"]
            else:
                if expected_revision or expected_hash:
                    raise ValueError("Güncel sipariş bulunamadı")
                identity = "LIVE-ORDER-" + uuid4().hex
                po = inputs[2]["fields"]
                if db.execute("SELECT 1 FROM live_order WHERE pilot_id=? AND buyer_key=? AND po_number_key=?",
                              (pilot["id"], po["buyer"].casefold(), po["po_number"].casefold())).fetchone():
                    raise ValueError("Bu alıcı / PO için yerel sipariş var; mevcut PO kaydını sürümleyin")
                db.execute("INSERT INTO live_order VALUES (?,?,?,?,?,?,?)",
                           (identity, pilot["id"], identities[2], po["po_number"].casefold(),
                            po["buyer"].casefold(), self.intake.actor, _stamp()))
            revision = expected_revision + 1
            db.execute("INSERT INTO live_order_revision VALUES (?,?,?,?,?,?,?,?,?)",
                       (identity, revision, _json(facts), encoded, digest, facts["document_id"],
                        facts["permission_document_id"], self.intake.actor, _stamp()))
            for item in inputs:
                db.execute("INSERT INTO live_order_input VALUES (?,?,?,?,?,?,?,?)",
                           (identity, revision, item["kind"], item["intake_id"], item["revision"],
                            item["payload_sha256"], item["source_id"], item["confirmation"]["sequence"]))
            return identity

    def current(self, db, identity):
        row = db.execute("SELECT * FROM live_order_revision WHERE order_id=? ORDER BY revision DESC LIMIT 1",
                         (identity,)).fetchone()
        if not row:
            raise ValueError("Canlı sipariş bulunamadı")
        return dict(row)

    def eligible(self, db, row):
        pilot = self.intake.gate(db)
        order = db.execute("SELECT * FROM live_order WHERE id=?", (row["order_id"],)).fetchone()
        if not order or order["pilot_id"] != pilot["id"]:
            raise ValueError("Sipariş pilotu eşleşmiyor")
        bundle = json.loads(row["bundle_json"])
        facts = json.loads(row["facts_json"])
        current = self.inputs(db, [item["intake_id"] for item in bundle["inputs"]])
        if current != bundle["inputs"]:
            raise ValueError("Bağlı kaynak sürümü veya onayı değişmiş; eski taslak kullanılamaz")
        bindings = [dict(item) for item in db.execute("SELECT * FROM live_order_input WHERE order_id=? "
                    "AND order_revision=?", (row["order_id"], row["revision"]))]
        expected = [{"order_id": row["order_id"], "order_revision": row["revision"], "kind": item["kind"],
            "intake_id": item["intake_id"], "intake_revision": item["revision"],
            "payload_sha256": item["payload_sha256"], "source_id": item["source_id"],
            "confirmation_sequence": item["confirmation"]["sequence"]} for item in current]
        if sorted(bindings, key=lambda v: v["kind"]) != sorted(expected, key=lambda v: v["kind"]):
            raise ValueError("Sipariş kaynak bağları doğrulanamadı")
        _future(facts["retention_until_utc"])
        if facts["document_id"] != row["document_id"] or facts["permission_document_id"] != row["permission_document_id"]:
            raise ValueError("Sevkiyat kaynağı eşleşmiyor")
        self.intake.read_document(db, row["document_id"])
        self.intake.read_document(db, row["permission_document_id"])
        if self.bundle(pilot, current, facts) != bundle or _hash(row["bundle_json"].encode()) != row["bundle_sha256"]:
            raise ValueError("Taslak içeriği doğrulanamadı")
        if bundle["requirements"]:
            raise ValueError("İnceleme gereksinimleri çözülmeli: " + "; ".join(bundle["requirements"]))
        return bundle

    def latest_review(self, db, identity):
        row = db.execute("SELECT * FROM live_order_review WHERE order_id=? ORDER BY sequence DESC LIMIT 1",
                         (identity,)).fetchone()
        return dict(row) if row else None

    def reviewed(self, db, row):
        self.eligible(db, row)
        pilot = self.intake.gate(db)
        review = self.latest_review(db, row["order_id"])
        if (not review or review["decision"] != "APPROVE" or review["revision"] != row["revision"]
                or review["bundle_sha256"] != row["bundle_sha256"]
                or review["approver_name"] != pilot["approver_name"]
                or review["approver_role"] != pilot["approver_role"]
                or review["authority_ref"] != pilot["authorization_ref"]):
            raise ValueError("Güncel tam taslak için kayıtlı insan onayı gerekli")
        self.intake.read_document(db, review["document_id"])
        return review

    def review(self, identity, revision, digest, expected_sequence, values, evidence):
        self.intake.require("RECORD_LIVE_CONFIRMATION")
        with self.intake.transaction() as db:
            row = self.current(db, identity)
            pilot = self.intake.gate(db)
            if (row["revision"], row["bundle_sha256"]) != (revision, digest):
                raise ValueError("Güncel tam sipariş sürümü gerekli")
            if values.get("decision") not in ("APPROVE", "REJECT", "REVOKE"):
                raise ValueError("Geçerli karar gerekli")
            if values.get("approver_name") != pilot["approver_name"] or values.get("reviewed") != "yes":
                raise ValueError("Kayıtlı ayrı onaylayanın tam taslak incelemesi gerekli")
            confirmed = _time(values.get("confirmed_at_utc"))
            if not _time(row["recorded_at_utc"]) <= confirmed <= _now():
                raise ValueError("Teyit zamanı bu sürümden sonra ve gelecekte olmayan bir zaman olmalı")
            if values["decision"] == "APPROVE":
                self.eligible(db, row)
            fields = [_text(values.get(key), key) for key in ("reason", "method", "evidence_ref")]
            document = self.intake.document(db, evidence)
            latest = self.latest_review(db, identity)
            sequence = latest["sequence"] if latest else 0
            if expected_sequence != sequence:
                # Exact retry is safe only while this is still the latest decision.
                if (latest and latest["revision"] == revision and latest["bundle_sha256"] == digest
                        and latest["decision"] == values["decision"] and latest["document_id"] == document
                        and [latest[key] for key in ("reason", "method", "evidence_ref")] == fields
                        and latest["confirmed_at_utc"] == confirmed.isoformat()):
                    return sequence
                raise ValueError("Taslak onay durumu değişti; yeniden inceleyin")
            cursor = db.execute("INSERT INTO live_order_review VALUES (NULL,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (identity, revision, digest, values["decision"], pilot["approver_name"], pilot["approver_role"],
                 pilot["authorization_ref"], *fields, confirmed.isoformat(), self.intake.actor, _stamp(), document,
                 "OPERATOR_ATTESTATION"))
            return cursor.lastrowid

    def measure(self, identity, revision, digest, values):
        baseline, elapsed = _amount(values.get("baseline_minutes", "")), _nonnegative(values.get("elapsed_minutes", ""))
        corrections = str(values.get("corrections", ""))
        if not re.fullmatch(r"[0-9]{1,6}", corrections):
            raise ValueError("Düzeltme sayısı negatif olmayan tam sayı olmalı")
        notes = _text(values.get("notes"), "Ölçüm ve yeniden işleme notu")
        baseline_ref = _text(values.get("baseline_ref"), "#13 manuel karşılaştırma kanıtı")
        with self.intake.transaction() as db:
            row = self.current(db, identity)
            if (row["revision"], row["bundle_sha256"]) != (revision, digest):
                raise ValueError("Güncel ölçüm sürümü gerekli")
            review = self.reviewed(db, row)
            values_tuple = (identity, revision, digest, review["sequence"], str(baseline), str(elapsed),
                            int(corrections), notes, baseline_ref, self.intake.actor)
            prior = db.execute("SELECT * FROM live_order_measurement WHERE order_id=? AND revision=? "
                               "AND review_sequence=?", (identity, revision, review["sequence"])).fetchone()
            if prior:
                if tuple(prior)[1:-1] != values_tuple:
                    raise ValueError("Bu onay için ölçüm zaten kayıtlı; düzeltme yeni inceleme gerektirir")
                return prior["sequence"]
            return db.execute("INSERT INTO live_order_measurement VALUES (NULL,?,?,?,?,?,?,?,?,?,?,?)",
                              (*values_tuple, _stamp())).lastrowid

    def view(self, identity=None):
        with self.intake.transaction(write=False) as db:
            result = {"orders": [dict(row) for row in db.execute("SELECT * FROM live_order ORDER BY rowid DESC")]}
            if not identity:
                return result
            row = self.current(db, identity)
            latest = self.latest_review(db, identity)
            try:
                self.eligible(db, row)
                status = "REVIEW_REQUIRED"
                if latest and latest["revision"] == row["revision"]:
                    status = latest["decision"]
                    if status == "APPROVE":
                        self.reviewed(db, row)
            except (ValueError, PermissionError) as exc:
                status = "BLOCKED: " + str(exc)
            result.update(row=row, bundle=json.loads(row["bundle_json"]), facts=json.loads(row["facts_json"]),
                status=status, sequence=latest["sequence"] if latest else 0,
                history=[dict(item) for item in db.execute("SELECT * FROM live_order_review WHERE order_id=? "
                    "ORDER BY sequence DESC", (identity,))],
                revisions=[dict(item) for item in db.execute("SELECT revision,bundle_sha256,recorded_at_utc "
                    "FROM live_order_revision WHERE order_id=? ORDER BY revision DESC", (identity,))],
                measurements=[dict(item) for item in db.execute("SELECT * FROM live_order_measurement "
                    "WHERE order_id=? ORDER BY sequence DESC", (identity,))],
                exports=[dict(item) for item in db.execute("SELECT * FROM live_order_export WHERE order_id=? "
                    "ORDER BY recorded_at_utc DESC", (identity,))])
            return result

    def prepare_export(self, identity, revision, digest, directory, expires_at):
        """Reserve the operator-declared browser destination BEFORE emitting any bytes.

        It stays PENDING until the exact private file is inspected by finish_export.
        The browser cannot prove where Save As wrote; outstanding copies stay visible.
        """
        directory = Path(directory).absolute()
        _private(directory, directory=True)
        expiry = _future(expires_at)
        with self.intake.transaction(write=False) as db:
            row = self.current(db, identity)
            if (row["revision"], row["bundle_sha256"]) != (revision, digest):
                raise ValueError("Güncel indirme sürümü gerekli")
            review = self.reviewed(db, row)
            bundle = json.loads(row["bundle_json"])
            deadlines = [json.loads(row["facts_json"])["retention_until_utc"],
                         self.intake.gate(db)["expires_at_utc"],
                         bundle["inputs"][1]["fields"]["valid_until_utc"]]
            deadlines += [item["permission"]["retention_until_utc"] for item in bundle["inputs"]]
            if any(_time(expiry) > _time(deadline) for deadline in deadlines):
                raise ValueError("Çıktı saklama süresi kaynak / yetki geçerliliğini aşamaz")
            artifact = render_bundle(bundle, digest, review)
        retention = self.intake.retention
        def prior_export(db, ledger):
            prior = db.execute("SELECT id,copy_id,artifact_sha256 FROM live_order_export WHERE order_id=? "
                               "AND revision=? AND review_sequence=?", (identity, revision, review["sequence"])).fetchall()
            for export in prior:
                copy = ledger.execute("SELECT * FROM recovery_copy WHERE id=?", (export["copy_id"],)).fetchone()
                if (copy and Path(copy["path"]).parent == directory and copy["expires_at"] == expiry
                        and copy["state"] in ("PENDING", "ACTIVE")):
                    if export["artifact_sha256"] != _hash(artifact) or copy["sha256"] not in (None, _hash(artifact)):
                        raise ValueError("Kayıtlı çıktı özeti eşleşmiyor")
                    ledger.execute("UPDATE recovery_copy SET sha256=? WHERE id=?", (_hash(artifact), copy["id"]))
                    return export["id"]
            return None

        def validate(db, ledger):
            db.row_factory = sqlite3.Row
            db.execute("BEGIN IMMEDIATE")
            retention.guard(db, ledger)
            current = self.current(db, identity)
            if ((current["revision"], current["bundle_sha256"]) != (revision, digest)
                    or self.reviewed(db, current) != review):
                raise ValueError("İndirme öncesi taslak / onay değişti")

        self.intake.require("RECORD_LIVE_INTAKE")
        retention.admin()
        with retention.ledger(exclusive=True) as ledger, closing(_connect(self.intake.path)) as db:
            validate(db, ledger)
            prior = prior_export(db, ledger)
            if prior:
                return prior
        export_id = "LIVE-EXPORT-" + uuid4().hex
        destination = directory / (export_id + ".html")
        # Durable reservation precedes main-DB export commit and any HTTP bytes.
        copy_id = retention.register_copy(destination, expiry, kind="EXPORT", pending=True)
        with retention.ledger(exclusive=True) as ledger, closing(_connect(self.intake.path)) as db:
            validate(db, ledger)
            prior = prior_export(db, ledger)
            if prior:
                return prior  # Concurrent duplicate; unused reservation remains visible PENDING.
            db.execute("INSERT INTO live_order_export VALUES (?,?,?,?,?,?,?,?,?)", (export_id, identity,
                revision, digest, review["sequence"], copy_id, _hash(artifact), self.intake.actor, _stamp()))
            retention.guard(db, ledger)
            db.commit()
            ledger.execute("UPDATE recovery_copy SET sha256=? WHERE id=?", (_hash(artifact), copy_id))
        return export_id

    def _export_data(self, db, ledger, export_id):
        export = db.execute("SELECT * FROM live_order_export WHERE id=?", (export_id,)).fetchone()
        if not export:
            raise ValueError("Kayıtlı çıktı bulunamadı")
        row = self.current(db, export["order_id"])
        review = self.reviewed(db, row)
        if (row["revision"], row["bundle_sha256"], review["sequence"]) != (
                export["revision"], export["bundle_sha256"], export["review_sequence"]):
            raise ValueError("Eski çıktı kullanılamaz; yeniden inceleyin")
        copy = ledger.execute("SELECT * FROM recovery_copy WHERE id=?", (export["copy_id"],)).fetchone()
        if not copy or copy["state"] not in ("PENDING", "ACTIVE"):
            raise ValueError("Çıktı kopyası kullanılamaz")
        _future(copy["expires_at"])
        artifact = render_bundle(json.loads(row["bundle_json"]), row["bundle_sha256"], review)
        if _hash(artifact) != export["artifact_sha256"] or copy["sha256"] != export["artifact_sha256"]:
            raise ValueError("Çıktı özeti doğrulanamadı")
        destination = Path(copy["path"])
        _private(destination.parent, directory=True)
        if destination.exists() or destination.is_symlink():
            _private(destination)
            if _sha256(destination) != export["artifact_sha256"]:
                raise ValueError("Kayıtlı çıktı dosyasının özeti eşleşmiyor")
        elif copy["state"] == "ACTIVE":
            raise ValueError("Doğrulanmış çıktı dosyası kayıp; saklama envanterini inceleyin")
        return artifact, dict(copy)

    def download(self, export_id):
        with self.intake.transaction(write=False) as db, self.intake.retention.ledger() as ledger:
            return self._export_data(db, ledger, export_id)

    def finish_export(self, export_id):
        self.intake.require("RECORD_LIVE_INTAKE")
        retention = self.intake.retention
        # One exclusive control lease spans validation, file inspection and registry update.
        with retention.ledger(exclusive=True) as ledger, closing(_connect(self.intake.path)) as db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN IMMEDIATE")
            retention.guard(db, ledger)
            artifact, copy = self._export_data(db, ledger, export_id)
            path = Path(copy["path"])
            _private(path.parent, directory=True)
            _private(path)
            if _sha256(path) != _hash(artifact):
                raise ValueError("İndirilen dosyanın yolu / özeti eşleşmiyor")
            retention.guard(db, ledger)
            ledger.execute("UPDATE recovery_copy SET state='ACTIVE' WHERE id=? AND state IN ('PENDING','ACTIVE')",
                           (copy["id"],))
            return copy["id"]

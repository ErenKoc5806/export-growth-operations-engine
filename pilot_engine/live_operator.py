"""Separate loopback intake workspace; full live workflow activation remains #68."""

import argparse
import secrets
import sys
import webbrowser
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer

from pilot_engine.live_intake import FIELDS, KINDS, LiveIntake, SELF_REVIEW, DISTINCT_REVIEW, review_label
from pilot_engine.operator_web import _field, _h, _layout, _utc, make_handler


def _timefield(name, label, value=""):
    if value:
        from pilot_engine.operator_web import _date_field

        return _date_field(name, label, value)
    return _field(name, label + " (İstanbul saati)", kind="datetime-local").replace(
        " required>", " step='1' required>")


def _hidden(name, value):
    return f"<input type='hidden' name='{_h(name)}' value='{_h(value)}'>"


def _proof(name, label):
    return _field(name, label + " (en çok 5 MB)", kind="file")


def _document(identity, label, *, purged=False):
    if purged:
        return _h(label) + " — silinmiş, kullanılamaz"
    return "<a href='/document?id=" + _h(identity) + "'>" + _h(label) + " — dosyayı indir</a>"


def _status(value):
    names = {"APPROVE": "Onaylandı", "REJECT": "Reddedildi", "REVOKE": "Onay geri çekildi",
             "REVIEW_REQUIRED": "İnceleme bekliyor"}
    return names.get(value, value.replace("BLOCKED:", "Engellendi:"))


class LiveOperator:
    intake_only = True

    def __init__(self, database, access=None, *, retention_ledger=None):
        self.intake = LiveIntake(database, access, retention_ledger=retention_ledger)
        self.session = secrets.token_urlsafe(32)
        self.csrf = secrets.token_urlsafe(32)

    def session_matches_actor(self):
        try:
            self.intake.require("READ_LIVE_INTAKE")
            return True
        except (OSError, RuntimeError):
            return False

    def form(self, action, fields, button):
        return ("<form method='post' enctype='multipart/form-data' action='" + _h(action) + "'>"
                + _hidden("csrf", self.csrf) + fields
                + "<button name='confirm' value='yes'>" + _h(button) + "</button></form>")

    def enrollment(self):
        fields = ("<label>İnceleme modeli<select name='review_model'>"
                  "<option value='" + SELF_REVIEW + "'>Tek kişi — kendi çalışmamı ayrıca incelerim</option>"
                  "<option value='" + DISTINCT_REVIEW + "'>Ayrı kayıtlı kişi — operatör beyanı</option></select></label>"
                  "<label><input type='checkbox' name='self_review_accepted' value='yes'>"
                  "Tek kişi modelinde işlemi yapan ve inceleyen aynı kişidir; bağımsız ikinci kişi kontrolü yoktur.</label>")
        for name, label in (("manufacturer_name", "Üretici"), ("authorization_ref", "Üretici yetki referansı"),
            ("operator_name", "Operatör adı"), ("approver_name", "İnceleyen adı (tek kişi modelinde operatörle aynı)"),
            ("approver_role", "Onaylayanın yetkili rolü"), ("authorizer_name", "Üretici adına izin veren"),
            ("it_reviewer", "Bilgisayar / depolama incelemesini kaydeden"), ("baseline", "Mevcut süreler ve pilot başarı ölçütleri"),
            ("backup_owner", "Yedek / saklama sorumlusu")):
            fields += _field(name, label)
        fields += _timefield("expires_at_utc", "Yetki geçerlilik sonu")
        fields += _proof("manufacturer_document", "Üretici yetkilendirme ve veri kullanım izni")
        fields += _proof("it_document", "IT kabulü / hesap ve depolama düzeni kanıtı")
        for name, label in (("manufacturer_accepted", "Üretici bu kişileri ve pilot kapsamını yetkilendirdi"),
            ("it_accepted", "Hesap, veri ve yedek düzeni incelendi ve kabul edildi"),
            ("trust_accepted", "Üretici / IT, veri sahibi ve yöneticinin SQLite kontrollerini aşabildiğini kabul etti"),
            ("permission_accepted", "Kaynakların kullanım izinleri ve saklama sorumluluğu teyit edildi")):
            fields += "<label><input type='checkbox' name='" + name + "' value='yes' required>" + label + "</label>"
        return ("<h2>Önce üretici ve IT yetkisi</h2><p>#13 açık: kanıtlar kaydedilmeden gerçek kaynak kabul edilmez. "
                "Bu kayıt, bağımsız kimlik doğrulaması veya otomatik izin değildir. "
                "Pilot kapsamı: tek üretici, HS6 732690, Almanya, EUR.</p>"
                + self.form("/enroll", fields, "Kanıtları inceledim; yetkilendirmeyi kaydet"))

    def edit(self, kind, snapshot=None):
        if kind not in FIELDS:
            raise ValueError("Kaynak türünü seçin")
        row = snapshot["row"] if snapshot else {}
        previous = snapshot["payload"]["fields"] if snapshot else {}
        fields = _hidden("kind", kind) + _hidden("intake_id", row.get("intake_id", ""))
        fields += _hidden("revision", row.get("revision", 0)) + _hidden("digest", row.get("payload_sha256", ""))
        for name, label in FIELDS[kind].items():
            fields += (_timefield(name, label, previous.get(name, "")) if name == "valid_until_utc"
                       else _field(name, label, previous.get(name, "1" if name == "price_unit" else "")))
        fields += "<h3>Kaynak ve kullanım izni</h3>"
        for name, label in (("source_ref", "Bu kaynak sürümünün özgün referansı"),
            ("permission_ref", "Kaynak kullanım izni referansı"), ("terms_ref", "Kaynak koşulları referansı"),
            ("processing_basis", "Veri işleme / kullanım dayanağı"),
            ("reviewer_name", "Kaynak iznini inceleyen kayıtlı onaylayan")):
            fields += _field(name, label)
        fields += _timefield("retention_until_utc", "Kaynak için saklama sonu")
        fields += _proof("source_document", "Özgün kaynak belgesi")
        fields += _proof("permission_document", "Kaynak kullanım izni ve koşulları kanıtı")
        return "<h2>" + _h(KINDS[kind]) + " — " + ("yeni sürüm" if row else "yeni kayıt") + "</h2>" + self.form(
            "/save", fields, "Kaynağı ve izin kanıtlarını kaydet; incelemeye al")

    def detail(self, data):
        row, payload, pilot = data["row"], data["payload"], data["pilot"]
        identity = row["intake_id"]
        body = "<h2>" + _h(KINDS[row["kind"]]) + "</h2><p class='badge'>" + _h(_status(data["status"])) + "</p>"
        body += "<p>Köken: LIVE · Sürüm: " + str(row["revision"]) + "</p><p>İçerik SHA-256: " + _h(row["payload_sha256"]) + "</p>"
        body += "<p>Kayıt OS aktörü: " + _h(row["actor_id"]) + " · " + _h(row["recorded_at_utc"]) + "</p>"
        for heading, entries in (("Tam içerik ve ticari koşullar", payload["fields"]),
                                  ("Kaynak izni ve saklama", payload["provenance"])):
            body += "<h3>" + heading + "</h3><dl>"
            for key, value in entries.items():
                label = FIELDS[row["kind"]].get(key, {"source_ref": "Kaynak referansı",
                    "permission_ref": "İzin referansı", "terms_ref": "Koşul referansı",
                    "processing_basis": "Kullanım dayanağı", "reviewer_name": "İnceleyen",
                    "retention_until_utc": "Saklama sonu"}.get(key, key))
                body += "<dt>" + _h(label) + "</dt><dd>" + _h(value) + "</dd>"
            body += "</dl>"
        with self.intake.transaction(write=False) as db:
            source = db.execute("SELECT * FROM live_source_permission WHERE source_id=?", (row["source_id"],)).fetchone()
            for doc_id, label in ((source["document_id"], "Özgün kaynak"),
                                  (source["permission_document_id"], "Kullanım izni kanıtı")):
                if self.intake.document_purged(db, doc_id):
                    body += "<p>" + _document(doc_id, label, purged=True) + "</p>"
                    continue
                doc = self.intake.read_document(db, doc_id)
                body += "<p>" + _document(doc_id, label) + " · SHA-256: " + _h(doc["sha256"]) + "</p>"
        body += "<h3>" + _h(review_label(pilot)) + "</h3><p>Onaylayan: " + _h(pilot["approver_name"]) + " · " + _h(pilot["approver_role"])
        body += " · Yetki: " + _h(pilot["authorization_ref"]) + "</p><p>OPERATÖR BEYANI: "
        body += "insanın teyidi kaydedilir; bağımsız onaylayan oturumu doğrulanmaz. Bu onay gönderime veya siparişe dönüşmez.</p>"
        fixed = _hidden("intake_id", identity) + _hidden("revision", row["revision"]) + _hidden("digest", row["payload_sha256"])
        fields = fixed + _hidden("sequence", data["sequence"])
        fields += _field("approver_name", "Teyidi veren kayıtlı insanın tam adı")
        fields += ("<label><span>Karar</span><select name='decision'><option value='APPROVE'>Onay</option>"
                   "<option value='REJECT'>Ret</option><option value='REVOKE'>Onayı geri çek</option></select></label>")
        fields += _field("reason", "Karar gerekçesi", kind="textarea") + _field("method", "Teyit yöntemi")
        fields += _field("evidence_ref", "Teyit kanıt referansı") + _timefield("confirmed_at_utc", "İnsanın teyit zamanı")
        fields += _proof("confirmation_document", "Bu sürüme ait insan teyidinin kanıtı")
        fields += "<label><input type='checkbox' name='reviewed' value='yes' required>Adı yazılan onaylayan, yukarıdaki tam sürümü, kaynakları ve koşulları inceledi ve bu kararı teyit etti.</label>"
        body += self.form("/confirm", fields, "İnsan teyidini bu tam sürüme kaydet")
        body += "<p><a href='/revise?id=" + _h(identity) + "'>Yeni sürüm oluştur — yeniden onay gerekir</a></p>"
        body += self.form("/stop-source", fixed + _field("reason", "Kaynak iznini geri çekme gerekçesi"), "Bu kaynağın iznini geri çek")
        body += "<h3>Karar geçmişi — eski sürümler korunur</h3>"
        for item in data["history"]:
            body += "<div class='card'><p>" + _h(_status(item["decision"])) + " · Sürüm " + str(item["revision"]) + " · " + _h(item["payload_sha256"]) + "</p>"
            labels = {"approver_name": "Onaylayan", "approver_role": "Yetkili rolü", "authority_ref": "Yetki referansı",
                "reason": "Gerekçe", "method": "Teyit yöntemi", "evidence_ref": "Kanıt referansı",
                "confirmed_at_utc": "Teyit zamanı (UTC)", "recorded_by_actor": "Kaydeden OS aktörü",
                "recorded_at_utc": "Kayıt zamanı (UTC)", "identity_basis": "Kimlik dayanağı"}
            for key in ("approver_name", "approver_role", "authority_ref", "reason", "method", "evidence_ref",
                        "confirmed_at_utc", "recorded_by_actor", "recorded_at_utc", "identity_basis"):
                body += "<p>" + _h(labels[key]) + ": " + _h(item[key]) + "</p>"
            body += _document(item["document_id"], "Teyit kanıtı",
                              purged=item["document_id"] in data["purged_document_ids"]) + "</div>"
        return body

    def page(self, path, query):
        if path == "/execute" or path.startswith("/execute/"):
            from pilot_engine.live_execute_web import ExecuteWorkspace

            return ExecuteWorkspace(self).page(path, query)
        def one(name):
            values = query.get(name, [])
            if len(values) != 1:
                raise ValueError("Tek bir kayıt seçin")
            return values[0]

        if path == "/document":
            with self.intake.transaction(write=False) as db:
                return self.intake.read_document(db, one("id"))["content"], True
        data = self.intake.view(one("id") if path in ("/record", "/revise") else None)
        body = "<p><a href='/'>← Kaynak kayıtları</a></p>"
        if data["blocked"]:
            body += "<p class='error'>" + _h(data["blocked"]) + "</p>"
        if path == "/" and not data["pilot"]:
            body += self.enrollment()
        elif path == "/":
            pilot = data["pilot"]
            body += "<p><a href='/execute'>Canlı Execute — yerel sipariş ve taslaklar</a></p>"
            body += "<h2>" + _h(pilot["manufacturer_name"]) + " — yetkili kaynaklar</h2>"
            body += "<p>" + _h(review_label(pilot)) + "</p>"
            body += "<p>Operatör: " + _h(pilot["operator_name"]) + " · Onaylayan: " + _h(pilot["approver_name"]) + "</p>"
            body += "<p>Yetki: " + _h(pilot["authorization_ref"]) + " · Geçerlilik: " + _h(pilot["expires_at_utc"]) + "</p>"
            with self.intake.transaction(write=False) as db:
                auth = db.execute("SELECT * FROM live_authorization WHERE pilot_id=?", (pilot["id"],)).fetchone()
                body += _document(auth["manufacturer_document_id"], "Üretici kabulü",
                    purged=auth["manufacturer_document_id"] in data["purged_document_ids"]) + " · " + _document(
                    auth["it_document_id"], "IT kabulü", purged=auth["it_document_id"] in data["purged_document_ids"])
            for kind, label in KINDS.items():
                body += "<p><a href='/new?kind=" + kind + "'>Yeni " + _h(label) + "</a></p>"
            for item in data["items"]:
                detail = self.intake.view(item["id"])
                fields = detail["payload"]["fields"]
                label = fields.get("product_name") or fields.get("company") or fields.get("po_number") or fields.get("sku")
                body += "<div class='card'><a href='/record?id=" + _h(item["id"]) + "'>" + _h(KINDS[item["kind"]]) + " — " + _h(label) + "</a> · Sürüm " + str(item["revision"]) + " · " + _h(_status(detail["status"])) + "</div>"
            body += self.form("/stop-pilot", _field("reason", "Pilot kaynak kabulünü durdurma gerekçesi"), "Kaynak kabulünü durdur")
        elif path in ("/new", "/revise"):
            body += self.edit(data["row"]["kind"] if path == "/revise" else one("kind"),
                              data if path == "/revise" else None)
        elif path == "/record":
            body += self.detail(data)
        else:
            raise ValueError("Kaynak kabulünde bu sayfa yok")
        return _layout(body, intake=True), False

    def execute(self, path, values, files):
        if values.get("confirm") != "yes":
            raise ValueError("İşlem için açık teyit gerekli")
        if path.startswith("/execute/"):
            from pilot_engine.live_execute_web import ExecuteWorkspace

            converted = dict(values)
            for key in ("retention_until_utc", "confirmed_at_utc", "expires_at_utc"):
                if key in converted:
                    parsed = datetime.fromisoformat(converted[key].replace("Z", "+00:00"))
                    converted[key] = parsed.astimezone(timezone.utc).isoformat() if parsed.tzinfo else _utc(converted[key])
            return ExecuteWorkspace(self).execute(path, converted, files)
        allowed = {"/enroll": {"manufacturer_document", "it_document"},
                   "/save": {"source_document", "permission_document"},
                   "/confirm": {"confirmation_document"}, "/stop-source": set(), "/stop-pilot": set()}
        if path not in allowed or set(files) != allowed[path]:
            raise ValueError("İşlem / kanıt dosyaları geçersiz")
        values = dict(values)
        for key in ("expires_at_utc", "retention_until_utc", "confirmed_at_utc", "valid_until_utc"):
            if key in values:
                parsed = datetime.fromisoformat(values[key].replace("Z", "+00:00"))
                values[key] = (parsed.astimezone(timezone.utc).isoformat() if parsed.tzinfo
                               else _utc(values[key]))
        if path == "/enroll":
            self.intake.enroll(values, files["manufacturer_document"], files["it_document"])
            return "/"
        if path == "/save":
            identity = self.intake.save(values["kind"], values, files["source_document"],
                files["permission_document"], intake_id=values.get("intake_id") or None,
                expected_revision=int(values.get("revision", 0)), expected_hash=values.get("digest", ""))
        elif path == "/confirm":
            identity = values["intake_id"]
            self.intake.confirm(identity, int(values["revision"]), values["digest"], int(values["sequence"]),
                                values, files["confirmation_document"])
        else:
            identity = values.get("intake_id") if path == "/stop-source" else None
            if path == "/stop-source" and not identity:
                raise ValueError("Kaynağı seçin")
            self.intake.stop(identity, int(values.get("revision", 0)), values.get("digest", ""), values.get("reason"))
            if not identity:
                return "/"
        return "/record?id=" + identity


def main():
    parser = argparse.ArgumentParser(description="Yerel yetkili kaynak kabulü; dış işlemler kapalı")
    parser.add_argument("--database", required=True)
    parser.add_argument("--retention-ledger", required=True)
    args = parser.parse_args()
    app = LiveOperator(args.database, retention_ledger=args.retention_ledger)
    with ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app)) as server:
        server.daemon_threads = True
        url = f"http://127.0.0.1:{server.server_port}/?token={app.session}"
        print("Kaynak kabul ekranı (gönderim / ERP / sevkiyat kapalı): " + url, flush=True)
        webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("Kaynak kabul ekranı kapatıldı", file=sys.stderr)


if __name__ == "__main__":
    main()

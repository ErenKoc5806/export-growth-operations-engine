"""Guided live Execute forms, hosted by the existing authenticated loopback app."""

from decimal import Decimal
import json
from pathlib import Path
from urllib.parse import urlencode

from pilot_engine.live_execute import FACTS, INPUT_KINDS, LiveExecute, render_bundle
from pilot_engine.live_intake import KINDS, review_label
from pilot_engine.live_operator import _hidden, _proof, _status, _timefield
from pilot_engine.operator_web import _field, _h, _layout


class ExecuteWorkspace:
    def __init__(self, app):
        self.app = app
        self.service = LiveExecute(app.intake)

    def selection_form(self):
        fields = ""
        with self.app.intake.transaction(write=False) as db:
            self.app.intake.gate(db)
            for kind in INPUT_KINDS:
                fields += "<label>" + _h(KINDS[kind]) + "<select required name='" + kind + "'><option value=''>Seçin</option>"
                for row in db.execute("SELECT id FROM live_intake WHERE kind=? ORDER BY rowid DESC", (kind,)):
                    current = self.app.intake.current(db, row[0])
                    data = json.loads(current["payload_json"])["fields"]
                    label = data.get("po_number") or data.get("product_name") or data.get("sku")
                    fields += "<option value='" + _h(row[0]) + "'>" + _h(label) + " · Sürüm " + str(current["revision"]) + "</option>"
                fields += "</select></label>"
        return "<h2>Canlı Execute — kaynak seçimi</h2><form method='get' action='/execute/new'>" + fields + "<button>Güncel kaynakları incele</button></form>"

    def edit_form(self, identities):
        inputs, digest = self.service.selection(identities)
        data = None
        # The PO chooses its existing local order; no internal order ID entry.
        for order in self.service.view()["orders"]:
            if order["po_intake_id"] == identities[2]:
                data = self.service.view(order["id"])
                break
        facts = data["facts"] if data else {}
        fields = _hidden("inputs_hash", digest)
        for kind, identity in zip(INPUT_KINDS, identities):
            fields += _hidden(kind, identity)
        fields += _hidden("revision", data["row"]["revision"] if data else 0)
        fields += _hidden("digest", data["row"]["bundle_sha256"] if data else "")
        body = "<h2>Yerel sipariş ve taslak bilgileri</h2><p>Eksikler inceleme listesine alınır; eksik / farklı bilgiyle onay ve indirme açılmaz.</p>"
        for item in inputs:
            body += "<p>" + _h(KINDS[item["kind"]]) + " · Sürüm " + str(item["revision"]) + " · " + _h(item["fields"]) + "</p>"
        for key, label in FACTS.items():
            fields += _field(key, label, facts.get(key, "")).replace(" required>", ">")
        for key, label in (("source_ref", "Sevkiyat / taraf bilgilerinin kaynak referansı"),
                           ("permission_ref", "Bu bilgilerin işlenme izni referansı"),
                           ("terms_ref", "Kullanım koşulu referansı"), ("processing_basis", "İşleme dayanağı"),
                           ("reviewer_name", "İzni inceleyen kayıtlı onaylayan")):
            fields += _field(key, label, facts.get(key, ""))
        fields += _timefield("retention_until_utc", "Bu bilgilerin saklama sonu", facts.get("retention_until_utc", ""))
        fields += _proof("facts_document", "Üreticinin taraf / sevkiyat / vergi / paketleme bilgi kanıtı")
        fields += _proof("permission_document", "Bu bilgilerin işlenme izni kanıtı")
        return body + self.app.form("/execute/save", fields, "Tam sürüm yerel taslak oluştur")

    def page(self, path, query):
        def one(name):
            values = query.get(name, [])
            if len(values) != 1:
                raise ValueError("Tek bir seçim gerekli")
            return values[0]

        if path == "/execute/download":
            identity = one("id")
            artifact, _ = self.service.download(identity)
            return artifact, identity + ".html"
        body = "<p><a href='/'>Kaynak kayıtları</a> · <a href='/execute'>Canlı Execute</a></p>"
        if path == "/execute":
            body += "<h2>Canlı Execute — yerel siparişler</h2><p><a href='/execute/new'>PO’dan yeni / düzeltilmiş taslak oluştur</a></p>"
            for order in self.service.view()["orders"]:
                data = self.service.view(order["id"])
                po = data["bundle"]["inputs"][2]["fields"]
                body += "<p><a href='/execute/order?id=" + _h(order["id"]) + "'>" + _h(po["po_number"]) + "</a> · " + _h(_status(data["status"])) + "</p>"
        elif path == "/execute/new":
            body += (self.edit_form([one(kind) for kind in INPUT_KINDS]) if all(kind in query for kind in INPUT_KINDS)
                     else self.selection_form())
        elif path == "/execute/history":
            with self.app.intake.transaction(write=False) as db:
                row = db.execute("SELECT bundle_json,bundle_sha256 FROM live_order_revision WHERE order_id=? AND revision=?",
                                 (one("id"), int(one("revision")))).fetchone()
                if not row:
                    raise ValueError("Eski taslak sürümü bulunamadı")
                body += "<h2>GEÇMİŞ SÜRÜM — KULLANILAMAZ / YENİ ONAY GEREKLİ</h2>"
                artifact = render_bundle(json.loads(row[0]), row[1]).decode()
                body += artifact.split("<body>", 1)[1].removesuffix("</body></html>")
        elif path == "/execute/order":
            identity = one("id")
            data = self.service.view(identity)
            row = data["row"]
            fixed = _hidden("order_id", identity) + _hidden("revision", row["revision"]) + _hidden("digest", row["bundle_sha256"])
            body += "<h2>Yerel sipariş — yayımlanmamış taslaklar</h2><p>" + _h(_status(data["status"])) + "</p>"
            body += "<p>Sürüm " + str(row["revision"]) + " · " + _h(row["bundle_sha256"]) + " · " + _h(row["recorded_at_utc"]) + "</p>"
            query_values = {item["kind"]: item["intake_id"] for item in data["bundle"]["inputs"]}
            body += "<p><a href='/execute/new?" + _h(urlencode(query_values)) + "'>Bilgileri düzelt / güncel kaynaklarla yeni sürüm hazırla</a></p>"
            artifact = render_bundle(data["bundle"], row["bundle_sha256"]).decode()
            body += artifact.split("<body>", 1)[1].removesuffix("</body></html>")
            fields = fixed + _hidden("sequence", data["sequence"])
            fields += _field("approver_name", "Kayıtlı inceleyen — seçilen pilot modeline göre")
            fields += "<label>Karar<select name='decision'><option>APPROVE</option><option>REJECT</option><option>REVOKE</option></select></label>"
            fields += _field("reviewed", "Tam içeriğin incelendiği teyidi: yes")
            for key, label in (("reason", "Karar ve PO farkları inceleme gerekçesi"), ("method", "Teyit yöntemi"), ("evidence_ref", "Tam sürüm teyit kanıtı referansı")):
                fields += _field(key, label)
            fields += _timefield("confirmed_at_utc", "Teyit zamanı") + _proof("review_document", "Bu tam taslağın onay kanıtı")
            body += "<h3>" + _h(review_label(data["pilot"])) + " — OPERATÖR BEYANI</h3>" + self.app.form("/execute/review", fields, "Tam sürüm kararı kaydet")
            fields = fixed
            for key, label in (("baseline_minutes", "#13 manuel süre karşılaştırması (dakika)"),
                    ("elapsed_minutes", "Bu PO için toplam gerçek operatör süresi (dakika)"),
                    ("corrections", "Düzeltme / yeniden işleme sayısı"), ("baseline_ref", "#13 manuel ölçüm kanıtı referansı"),
                    ("notes", "Süre ve düzeltmelerin kapsamı / ölçüm notu")):
                fields += _field(key, label)
            body += "<h3>İlk fayda ölçümü</h3>" + self.app.form("/execute/measure", fields, "Bu onay için ölçümü kaydet")
            for item in data["measurements"]:
                saved = Decimal(item["baseline_minutes"]) - Decimal(item["elapsed_minutes"])
                body += "<p>Sürüm " + str(item["revision"]) + ": manuel " + _h(item["baseline_minutes"]) + " dk · operatör " + _h(item["elapsed_minutes"]) + " dk · fark " + _h(saved) + " dk · düzeltme " + str(item["corrections"]) + " · " + _h(item["notes"]) + "</p>"
            body += "<h3>İncelenmiş taslakları indir</h3><p>Önce özel hedef klasörü ve saklama süresini kaydedin. Tarayıcıda Farklı Kaydet ile aşağıda verilen TAM yolu seçin. Dosya doğrulanana kadar kopya BEKLİYOR kalır. Başka dosya / çıktı kopyalarını da saklama envanterine kaydedin.</p>"
            body += self.app.form("/execute/export", fixed + _field("directory", "Önceden hazırlanmış özel indirme klasörünün tam yolu")
                + _timefield("expires_at_utc", "Bu çıktı kopyasının saklama sonu"), "İndirme hedefini kaydet")
            with self.app.intake.retention.ledger() as ledger:
                for item in data["exports"]:
                    copy = ledger.execute("SELECT * FROM recovery_copy WHERE id=?", (item["copy_id"],)).fetchone()
                    if not copy:
                        continue
                    body += "<p>Çıktı sürümü " + str(item["revision"]) + " · " + _h(copy["state"]) + " · hedef: " + _h(copy["path"]) + " · sorumlu: " + _h(copy["owner"]) + " · son: " + _h(copy["expires_at"]) + "</p>"
                    body += "<p><a download='" + _h(Path(copy["path"]).name) + "' href='/execute/download?id=" + _h(item["id"]) + "'>Üç DRAFT belgeyi tek yazdırılabilir HTML dosyası olarak indir</a></p>"
                    body += self.app.form("/execute/finish-export", _hidden("export_id", item["id"]) + _hidden("order_id", identity), "Kaydettiğim özel dosyayı doğrula")
            body += "<h3>Değişmez sürüm ve karar geçmişi</h3>"
            for item in data["revisions"]:
                body += "<p><a href='/execute/history?id=" + _h(identity) + "&amp;revision=" + str(item["revision"]) + "'>Geçmiş sürüm " + str(item["revision"]) + "</a> · " + _h(item["bundle_sha256"]) + " · " + _h(item["recorded_at_utc"]) + "</p>"
            for item in data["history"]:
                body += "<p>" + _h(_status(item["decision"])) + " · Sürüm " + str(item["revision"]) + " · " + _h(item["approver_name"]) + " / " + _h(item["approver_role"]) + " · OS: " + _h(item["actor_id"]) + " · " + _h(item["reason"]) + " · " + _h(item["confirmed_at_utc"]) + "</p>"
        else:
            raise ValueError("Canlı Execute sayfası bulunamadı")
        return _layout(body, intake=True), False

    def execute(self, path, values, files):
        if values.get("confirm") != "yes":
            raise ValueError("Açık işlem teyidi gerekli")
        allowed = {"/execute/save": {"facts_document", "permission_document"},
                   "/execute/review": {"review_document"}, "/execute/measure": set(),
                   "/execute/export": set(), "/execute/finish-export": set()}
        if path not in allowed or set(files) != allowed[path]:
            raise ValueError("Canlı Execute işlemi / kanıt dosyaları geçersiz")
        if path == "/execute/save":
            identity = self.service.save([values[kind] for kind in INPUT_KINDS], values["inputs_hash"], values,
                files["facts_document"], files["permission_document"], expected_revision=int(values["revision"]),
                expected_hash=values.get("digest", ""))
        else:
            identity = values["order_id"]
            if path == "/execute/finish-export":
                self.service.finish_export(values["export_id"])
            elif path == "/execute/review":
                self.service.review(identity, int(values["revision"]), values["digest"], int(values["sequence"]),
                                    values, files["review_document"])
            elif path == "/execute/measure":
                self.service.measure(identity, int(values["revision"]), values["digest"], values)
            else:
                self.service.prepare_export(identity, int(values["revision"]), values["digest"],
                                            values["directory"], values["expires_at_utc"])
        return "/execute/order?id=" + identity

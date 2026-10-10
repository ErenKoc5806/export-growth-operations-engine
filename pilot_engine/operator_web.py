"""Button-driven, localhost-only visual walkthrough of a synthetic case."""

from __future__ import annotations

import html
import json
import os
import secrets
import sys
import webbrowser
from contextlib import closing
from datetime import datetime, timedelta, timezone
from email.parser import BytesParser
from email.policy import default
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from pilot_engine.config import AppConfig, Environment
from pilot_engine.execute_documents import ExecuteDocuments
from pilot_engine.execute_operations import ExecuteOperations
from pilot_engine.execute_order import ExecuteOrder
from pilot_engine.find_handoff import FindHandoff
from pilot_engine.inbound import InboundResponses
from pilot_engine.manual_contact import ManualContact
from pilot_engine.quotations import Quotations
from pilot_engine.rfq import SellRFQ
from pilot_engine.sell_delivery import CaptureMailbox, SellDelivery
from pilot_engine.sell_drafts import SellDrafts
from pilot_engine.synthetic_demo import create_demo_handoff


def _h(value: object) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _utc(value: str) -> str:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("Tarih saat dilimi içermeli; örnek: 2027-01-01T12:00:00Z")
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")


def _future(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).replace(
        hour=12, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")


def _field(name: str, label: str, value: object = "", *, kind: str = "text",
           choices: tuple[str, ...] = (), required: bool = True) -> str:
    if kind == "select":
        options = "".join(f'<option value="{_h(x)}"{" selected" if x == value else ""}>'
                          f'{_h(x)}</option>' for x in choices)
        control = f'<select name="{_h(name)}">{options}</select>'
    elif kind == "textarea":
        control = f'<textarea name="{_h(name)}" rows="3">{_h(value)}</textarea>'
    else:
        control = (f'<input name="{_h(name)}" type="{_h(kind)}" '
                   f'value="{_h(value)}" {"step=any" if kind == "number" else ""} '
                   f'{"required" if required else ""}>')
    return f'<label><span>{_h(label)}</span>{control}</label>'


def _first(db: object, sql: str, *args: str) -> str | None:
    row = db.execute(sql, args).fetchone()
    return row[0] if row else None


class OperatorWeb:
    """View and mutation boundaries share the same service layer as the CLI."""

    def __init__(self, config: AppConfig):
        if config.environment is Environment.PILOT:
            raise ValueError("Görsel arayüz yalnızca sentetik modda çalışır; canlı mod #42'de")
        self.config = config
        self.store = config.open_store()
        self.session = secrets.token_urlsafe(32)
        self.csrf = secrets.token_urlsafe(32)

    def handoffs(self) -> list[dict]:
        with closing(self.store._connect()) as db:
            ids = [row[0] for row in db.execute("SELECT id FROM find_handoff ORDER BY rowid DESC")]
        return [FindHandoff(self.store).read(value) for value in ids]

    def chain(self, handoff_id: str) -> dict:
        handoff = FindHandoff(self.store).read(handoff_id)
        if handoff is None:
            raise ValueError("Find devri bulunamadı")
        result = {"handoff": handoff}
        with closing(self.store._connect()) as db:
            ids = {}
            ids["opportunity"] = _first(db, """SELECT id FROM sell_opportunity
                WHERE product_id = ? AND candidate_id = ? ORDER BY rowid DESC LIMIT 1""",
                handoff["product_id"], handoff["candidate_id"])
            ids["draft"] = _first(db, "SELECT id FROM sell_draft WHERE handoff_id = ? "
                                  "ORDER BY rowid DESC LIMIT 1", handoff_id)
            ids["attempt"] = (_first(db, """SELECT id FROM sell_send_attempt
                WHERE draft_id = ? ORDER BY rowid DESC LIMIT 1""", ids["draft"])
                              if ids["draft"] else None)
            ids["inbound"] = (_first(db, """SELECT id FROM sell_inbound_message
                WHERE matched_opportunity_id = ? ORDER BY rowid DESC LIMIT 1""",
                ids["opportunity"]) if ids["opportunity"] else None)
            ids["rfq"] = (_first(db, "SELECT id FROM sell_rfq WHERE inbound_id = ?",
                                 ids["inbound"]) if ids["inbound"] else None)
            ids["authority"] = _first(db, """SELECT id FROM sell_price_authority
                WHERE product_id = ? ORDER BY rowid DESC LIMIT 1""", handoff["product_id"])
            ids["quote"] = (_first(db, "SELECT id FROM sell_quotation WHERE rfq_id = ? "
                                   "ORDER BY rowid DESC LIMIT 1", ids["rfq"])
                            if ids["rfq"] else None)
            ids["po"] = (_first(db, "SELECT id FROM execute_po WHERE quotation_id = ? "
                                "ORDER BY rowid DESC LIMIT 1", ids["quote"])
                         if ids["quote"] else None)
            ids["order"] = (_first(db, "SELECT id FROM execute_local_order WHERE po_id = ?",
                                   ids["po"]) if ids["po"] else None)
            ids["documents"] = ({row["kind"].lower(): row["id"] for row in db.execute(
                "SELECT kind, id FROM execute_document WHERE order_id = ?", (ids["order"],))}
                                if ids["order"] else {})
            product = db.execute("SELECT sku, unit FROM product WHERE id = ?",
                                 (handoff["product_id"],)).fetchone()
            result["product"] = dict(product)
        result["ids"] = ids
        reads = {"draft": SellDrafts, "attempt": SellDelivery, "inbound": InboundResponses,
                 "rfq": SellRFQ, "quote": Quotations, "po": ExecuteOrder}
        for key, service in reads.items():
            if ids[key]:
                result[key] = service(self.store).read(ids[key])
        if ids["draft"]:
            draft = result["draft"]
            decision = None
            with closing(self.store._connect()) as db:
                decision = db.execute("""SELECT decision, envelope_sha256 FROM sell_send_decision
                    WHERE draft_id = ? AND revision = ? ORDER BY sequence DESC LIMIT 1""",
                    (ids["draft"], draft["revision"])).fetchone()
            preview = SellDelivery(self.store).preview(ids["draft"], draft["revision"])
            result["send_approved"] = bool(decision and decision["decision"] == "APPROVE"
                                           and decision["envelope_sha256"] == preview["envelope_sha256"])
        if ids["inbound"]:
            raw_ref = result["inbound"]["raw_ref"]
            path = self.config.data_dir / "synthetic-inbound" / (raw_ref + ".txt")
            if path.is_file() and not path.is_symlink():
                result["inbound_text"] = path.read_text(encoding="utf-8")
        if ids["order"]:
            result["case"] = ExecuteDocuments(self.store).case_summary(ids["order"])
        result["documents"] = {key: ExecuteDocuments(self.store).read(value)
                               for key, value in ids["documents"].items()}
        return result

    @staticmethod
    def step(case: dict) -> str:
        ids = case["ids"]
        if case["handoff"]["status"] != "CURRENT_RESEARCH":
            return "blocked"
        if not ids["opportunity"]:
            return "opportunity"
        if not ids["draft"]:
            return "draft"
        if case["draft"]["status"] != "CURRENT_DRAFT":
            return "blocked"
        if not ids["attempt"]:
            return "send" if not case.get("send_approved") else "capture"
        if not ids["inbound"]:
            return "inbound"
        if case["inbound"]["classification"] == "OPT_OUT":
            return "blocked"
        if case["inbound"]["classification"] != "RFQ_CANDIDATE":
            return "review-inbound"
        if not ids["rfq"]:
            return "rfq"
        if case["rfq"]["status"] == "DRAFT":
            return "accept-rfq"
        if case["rfq"]["status"] != "ACCEPTED_SYNTHETIC":
            return "blocked"
        if not ids["authority"]:
            return "price"
        if not ids["quote"]:
            return "quote"
        if case["quote"]["status"] == "DRAFT":
            return "approve-quote"
        if case["quote"]["status"] != "APPROVED_SYNTHETIC" and not ids["order"]:
            return "blocked"
        if not ids["po"]:
            return "po"
        if case["po"]["status"] == "DRAFT":
            return "approve-po"
        if not ids["order"] or case["case"]["operations"]["order"]["status"] != "LOCAL_ORDER_SYNTHETIC":
            return "blocked"
        operations = case["case"]["operations"]
        for kind, step in (("manual_erp_handoff", "erp"), ("readiness", "readiness"),
                           ("freight_plan", "freight")):
            if operations[kind] is None:
                return step
        for kind in ("invoice", "packing", "checklist"):
            if kind not in case["documents"]:
                return kind
            if case["documents"][kind]["status"] != "REVIEWED_SYNTHETIC":
                return "review-" + kind
        return "complete"


STYLE = """
body{margin:0;background:#f3f5f8;color:#152333;font:16px/1.5 system-ui,sans-serif}
header{background:#17283e;color:white;padding:22px max(24px,calc((100vw - 1100px)/2))}
header h1{font-size:22px;margin:0}header p{margin:3px 0;color:#bfd4e7}
main{max-width:1100px;margin:28px auto;padding:0 24px}.banner{background:#fff2cc;border:1px solid #e8cf74;padding:12px 16px;border-radius:10px}
.grid{display:grid;grid-template-columns:290px 1fr;gap:22px;margin-top:22px}
.panel,.card{background:#fff;border:1px solid #dce3eb;border-radius:14px;padding:22px;box-shadow:0 3px 12px #1b39510d}
.card{margin:12px 0}.card h3{margin:0 0 5px}h2{margin:0 0 12px;font-size:22px}
.muted{color:#5d6e7d}.badge{display:inline-block;background:#e6effa;color:#225785;border-radius:999px;padding:3px 10px;font-size:13px}
.warn{background:#fff3e4;color:#8a4a11}.good{background:#e3f5ea;color:#17663b}
label{display:block;margin:12px 0;font-weight:600}label span{display:block;margin-bottom:5px}
input,select,textarea{box-sizing:border-box;width:100%;padding:10px;border:1px solid #b9c8d5;border-radius:8px;font:inherit}
input[type=checkbox]{width:auto;margin-right:8px}.check{font-weight:400}
button,.button{display:inline-block;background:#1759a0;color:white;border:0;border-radius:9px;padding:11px 18px;font:inherit;font-weight:700;cursor:pointer;text-decoration:none}
a{color:#1759a0}.row{border-bottom:1px solid #e4e9ee;padding:12px 0}.row:last-child{border:0}
.error{background:#fce9e8;border:1px solid #d98380;padding:12px;border-radius:8px}
.evidence{background:#f7f9fb;border-radius:10px;padding:12px;margin:12px 0;white-space:pre-wrap;overflow-wrap:anywhere}
@media(max-width:760px){.grid{grid-template-columns:1fr}header{padding:20px 24px}}
"""

TITLES = {
    "opportunity": "Satış fırsatını aç", "draft": "İletişim taslağı oluştur",
    "send": "İleti içeriğini onayla", "capture": "Sentetik gönderimi kaydet",
    "inbound": "Örnek alıcı yanıtı kaydet", "review-inbound": "Yanıtı sınıflandır",
    "rfq": "RFQ bilgilerini gir", "accept-rfq": "RFQ'yu inceleyip kabul et",
    "price": "Üretici test fiyatını kaydet", "quote": "Teklif taslağı oluştur",
    "approve-quote": "Teklif taslağını onayla", "po": "Örnek müşteri PO'sunu gir",
    "approve-po": "PO'yu inceleyip yerel siparişi aç", "erp": "ERP için manuel devir kaydet",
    "readiness": "Mal hazırlık durumunu kaydet", "freight": "Navlun talebini planla",
    "invoice": "Fatura taslağı", "packing": "Paketleme taslağı",
    "checklist": "Sevkiyat belge listesi", "review-invoice": "Fatura taslağını incele",
    "review-packing": "Paketleme taslağını incele",
    "review-checklist": "Belge listesini incele", "blocked": "İnceleme gerekli",
    "complete": "Teknik vaka tamamlandı",
}


def _form_for(case: dict, step: str) -> tuple[str, str]:
    ids = case["ids"]
    fields = ""
    evidence = ""
    if step == "opportunity":
        evidence = "Onaylı Find devri bu şirkete ve ürün profiline bağlıdır."
    elif step == "draft":
        fields += _field("sender_name", "Gönderen adı")
        fields += _field("sender_address", "Gönderen örnek e-posta adresi", "seller@example.com")
        fields += _field("language", "Dil", "en", kind="select", choices=("en", "de", "tr"))
        evidence = "Metindeki tüm iddiaları ve alıcı adresini ayrıca kontrol edin. Otomatik iddia doğrulaması yoktur."
    elif step == "send":
        preview = SellDelivery(case["store"]).preview(ids["draft"], case["draft"]["revision"])
        evidence = (f"Alıcı: {preview['recipient']}\nGönderen: {preview['sender_name']} "
                    f"<{preview['sender_address']}>\nKonu: {preview['subject']}\n\n"
                    f"{preview['body']}\n\nRevizyon: {preview['revision']}\n"
                    f"İçerik özeti: {preview['envelope_sha256']}")
        fields += _field("reason", "Onay gerekçesi")
        fields += '<label class="check"><input type="checkbox" name="reviewed_claims" value="yes" required>Metni, alıcıyı ve iddiaları insan olarak inceledim.</label>'
    elif step == "capture":
        evidence = "Bu işlem yalnızca bellek içi test posta kutusuna kaydeder; teslimat değildir."
    elif step == "inbound":
        fields += _field("message", "Örnek alıcı yanıtı (sentetik metin)", kind="textarea")
        evidence = "Gerçek e-posta okunmaz. Yazdığınız örnek yanıt yerel kanıt dosyasında tutulur."
    elif step == "review-inbound":
        fields += _field("classification", "Yanıt sınıfı", "RFQ_CANDIDATE", kind="select",
                         choices=("RFQ_CANDIDATE", "INTEREST", "REJECTION", "OTHER", "OPT_OUT"))
        fields += _field("explanation", "Sınıflandırma gerekçesi", kind="textarea")
        evidence = (f"Yanıt kaynağı: {case['inbound']['raw_ref']}\n\n"
                    + case.get("inbound_text", "Yanıt metni bulunamadı; kaynağı inceleyin."))
    elif step == "rfq":
        fields += _field("customer_reference", "Müşteri RFQ referansı", "SYN-UI-RFQ")
        fields += _field("specification", "Talep edilen ürün / teknik tanım")
        fields += _field("quantity", "Adet / miktar", kind="number")
        fields += _field("requested_terms", "Talep edilen teslim koşulu", kind="text", required=False)
        evidence = (f"Ürün: {case['product']['sku']} · Birim: {case['product']['unit']} · "
                    f"Hedef: {case['handoff']['market_country']}. Miktar üretici MOQ'sunu karşılamalı.")
    elif step == "accept-rfq":
        fields += _field("reason", "RFQ inceleme gerekçesi")
        evidence = json.dumps(case["rfq"]["payload"], ensure_ascii=False, indent=2)
    elif step == "price":
        fields += _field("unit_price_text", "Test birim fiyatı (EUR)", kind="number")
        fields += _field("valid_until_utc", "Fiyat geçerliliği (UTC, saat dilimiyle)", _future(180))
        fields += _field("price_document", "Sentetik fiyat belgesi", kind="file")
        evidence = "Belge yalnızca hash için okunur. Gerçek fiyat veya müşteri dosyası yüklemeyin."
    elif step == "quote":
        fields += _field("valid_until_utc", "Teklif geçerliliği (UTC, saat dilimiyle)", _future(90))
        fields += _field("exclusions", "Hariç tutulanlar", "Sentetik teklif; gerçek gönderim yok")
        evidence = f"Kaynak RFQ: {case['rfq']['payload']['specification']} · {case['rfq']['payload']['quantity']} {case['rfq']['payload']['unit']}"
    elif step == "approve-quote":
        fields += _field("reason", "Teklif inceleme gerekçesi")
        evidence = json.dumps(case["quote"]["payload"], ensure_ascii=False, indent=2)
    elif step == "po":
        with closing(case["store"]._connect()) as db:
            _, expected = ExecuteOrder(case["store"])._expected(db, ids["quote"])
        for key, label in (("buyer", "Alıcı"), ("seller", "Satıcı"), ("sku", "SKU"),
                           ("quantity", "Miktar"), ("unit", "Birim"),
                           ("unit_price", "Birim fiyat"), ("currency", "Para birimi"),
                           ("total", "Toplam"), ("incoterm_code", "Incoterm"),
                           ("incoterm_place", "Teslim yeri")):
            fields += _field(key, label, expected[key])
        fields += _field("description", "PO ürün açıklaması", case["quote"]["payload"]["specification"])
        fields += _field("requested_delivery_at_utc", "İstenen teslim tarihi (UTC)", _future(120))
        fields += _field("po_document", "Sentetik PO dosyası", kind="file")
        evidence = "Alanlar tekliften önerildi. PO dosyasıyla karşılaştırarak onaylayın. Uyuşmazlıklar onayı durdurur."
    elif step == "approve-po":
        fields += _field("reason", "PO onay gerekçesi")
        evidence = (json.dumps(case["po"]["payload"], ensure_ascii=False, indent=2)
                    + "\nUyuşmazlıklar: " + ", ".join(case["po"]["differences"]))
    elif step == "erp":
        fields += _field("customer_mapping_ref", "ERP müşteri eşleme referansı", "SYN-CUSTOMER-MAP")
        fields += _field("tax_mapping_ref", "Vergi eşleme referansı", "SYN-TAX-MAP")
        fields += _field("handed_to", "Devir alan kişi", "Örnek operatör")
        evidence = "Manuel devir kaydıdır; ERP'de sipariş açıldığı anlamına gelmez."
    elif step == "readiness":
        fields += _field("planned_at_utc", "Planlanan hazırlık zamanı (UTC)", _future(100))
        fields += _field("reason", "Kaynak ve durum açıklaması", "Üreticiden sentetik teyit bekleniyor")
        evidence = "PENDING gözlemi fiziksel hazır olduğunu iddia etmez."
    elif step == "freight":
        fields += _field("pickup", "Alım yeri", "Örnek fabrika")
        fields += _field("delivery", "Teslim yeri", case["case"]["operations"]["order"]["payload"]["incoterm_place"])
        fields += _field("packages", "Koli sayısı", "1", kind="number")
        fields += _field("net_weight_kg", "Net kg", kind="number")
        fields += _field("gross_weight_kg", "Brüt kg", kind="number")
        fields += _field("dimensions", "Ölçüler", "100 x 40 x 30 cm")
        evidence = "Navlun talebi planlanır; gerçek booking yapılmaz."
    elif step == "invoice":
        fields += _field("exporter_legal_id", "İhracatçı yasal kayıt referansı", "SYN-TR-123")
        fields += _field("buyer_legal_id", "Alıcı yasal kayıt referansı", "SYN-DE-456")
        fields += _field("tax_review_ref", "Vergi inceleme referansı", "SYN-TAX-REVIEW")
        evidence = "Ticari fatura taslağıdır; düzenlenmiş yasal fatura değildir."
    elif step == "packing":
        plan = case["case"]["operations"]["freight_plan"]["payload"]
        fields += _field("quantity", "Paketteki toplam miktar", case["case"]["operations"]["order"]["payload"]["quantity"])
        fields += _field("net_weight_kg", "Net kg", plan["net_weight_kg"])
        fields += _field("gross_weight_kg", "Brüt kg", plan["gross_weight_kg"])
        fields += _field("dimensions", "Ölçüler", plan["dimensions"])
        fields += _field("marks", "Paket işaretleri", "SYN-MARK-1")
        evidence = "İlk görsel akış tek paketli sentetik vakayı destekler. Miktar ve ağırlıklar navlun planıyla eşleşmeli."
    elif step == "checklist":
        fields += _field("name", "Belge adı", "Origin evidence")
        fields += _field("status", "Gereklilik", "NOT_REQUIRED", kind="select",
                         choices=("REQUIRED", "NOT_REQUIRED", "UNKNOWN"))
        fields += _field("owner", "İnceleyen", "Örnek operatör")
        evidence = "Gereklilik sentetik operatör kararıdır; gerçek mevzuat kontrolü değildir."
    elif step.startswith("review-"):
        kind = step.removeprefix("review-")
        fields += _field("reason", "Belge taslağı inceleme gerekçesi")
        evidence = json.dumps(case["documents"][kind]["payload"], ensure_ascii=False, indent=2)
    if step not in ("blocked", "complete"):
        fields += '<label class="check"><input type="checkbox" name="confirm" value="yes" required>Gösterilen kaydı ve bu işlemi inceledim.</label>'
    return fields, evidence


def _execute(case: dict, step: str, values: dict[str, str], files: dict[str, bytes]) -> None:
    if values.get("confirm") != "yes":
        raise ValueError("İşlem için açık onay gerekli")
    store = case["store"]
    ids = case["ids"]
    def required(name: str) -> str:
        value = values.get(name, "").strip()
        if not value:
            raise ValueError(f"{name} alanını doldurun")
        return value
    def evidence() -> str:
        return "SYN-UI-" + uuid4().hex[:16]

    if step == "opportunity":
        ManualContact(store).open_opportunity(case["handoff"]["id"])
    elif step == "draft":
        SellDrafts(store).create(case["handoff"]["id"], sender_name=required("sender_name"),
                                 sender_address=required("sender_address"),
                                 language=required("language"), claim_refs=[])
    elif step == "send":
        draft = case["draft"]
        preview = SellDelivery(store).preview(ids["draft"], draft["revision"])
        SellDelivery(store).decide(ids["draft"], draft["revision"], "APPROVE",
                                   required("reason"),
                                   expected_envelope_sha256=preview["envelope_sha256"],
                                   reviewed_claims=values.get("reviewed_claims") == "yes")
    elif step == "capture":
        SellDelivery(store).dispatch_synthetic(ids["draft"], case["draft"]["revision"],
                                                CaptureMailbox())
    elif step == "inbound":
        message = required("message")
        if len(message) > 10000:
            raise ValueError("Örnek yanıt 10000 karakteri aşamaz")
        raw_ref = evidence()
        directory = case["data_dir"] / "synthetic-inbound"
        directory.mkdir(mode=0o700, exist_ok=True)
        path = directory / (raw_ref + ".txt")
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                             | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(message)
                stream.flush()
                os.fsync(stream.fileno())
            InboundResponses(store).record_inbound(source_ref=evidence(), raw_ref=raw_ref,
                received_at_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                channel="EMAIL", provider_message_id=case["attempt"]["provider_message_id"])
        except Exception:
            path.unlink(missing_ok=True)
            raise
    elif step == "review-inbound":
        InboundResponses(store).review(ids["inbound"], ids["opportunity"],
            classification=required("classification"), evidence_ref=case["inbound"]["raw_ref"],
            explanation=required("explanation"))
    elif step == "rfq":
        SellRFQ(store).save(ids["inbound"], {"customer_reference": required("customer_reference"),
            "sku": case["product"]["sku"], "specification": required("specification"),
            "quantity": required("quantity"), "unit": case["product"]["unit"],
            "destination": case["handoff"]["market_country"],
            "requested_terms": {"delivery": values.get("requested_terms", "").strip()}})
    elif step == "accept-rfq":
        SellRFQ(store).decide(ids["rfq"], case["rfq"]["revision"], "ACCEPT", required("reason"))
    elif step == "price":
        content = files.get("price_document", b"")
        Quotations(store).register_price_authority(case["handoff"]["product_id"],
            source_ref=evidence(), content=content, sku=case["product"]["sku"],
            currency=store.scope.currency, unit_price_text=required("unit_price_text"),
            valid_until_utc=_utc(required("valid_until_utc")))
    elif step == "quote":
        Quotations(store).save(ids["rfq"], ids["authority"],
            valid_until_utc=_utc(required("valid_until_utc")), exclusions=required("exclusions"))
    elif step == "approve-quote":
        quote = case["quote"]
        Quotations(store).decide(ids["quote"], quote["revision"], "APPROVE",
            expected_payload_sha256=quote["payload_sha256"], reason=required("reason"))
    elif step == "po":
        keys = ("buyer", "seller", "sku", "description", "quantity", "unit", "unit_price",
                "currency", "total", "incoterm_code", "incoterm_place")
        payload = {key: required(key) for key in keys}
        payload["requested_delivery_at_utc"] = _utc(required("requested_delivery_at_utc"))
        ExecuteOrder(store).save(ids["quote"], source_ref=evidence(), original_ref=evidence(),
            original=files.get("po_document", b""),
            received_at_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            payload=payload)
    elif step == "approve-po":
        po = case["po"]
        ExecuteOrder(store).decide(ids["po"], po["revision"], "APPROVE",
            expected_payload_sha256=po["payload_sha256"], reason=required("reason"))
    elif step == "erp":
        ExecuteOperations(store).manual_erp_handoff(ids["order"], operation_key=evidence(),
            customer_mapping_ref=required("customer_mapping_ref"),
            tax_mapping_ref=required("tax_mapping_ref"), handed_to=required("handed_to"),
            evidence_ref=evidence())
    elif step == "readiness":
        ExecuteOperations(store).readiness(ids["order"], operation_key=evidence(),
            status="PENDING", planned_at_utc=_utc(required("planned_at_utc")),
            actual_at_utc=None, quantity=None, source_ref=evidence(), reason=required("reason"))
    elif step == "freight":
        ExecuteOperations(store).plan_freight(ids["order"], operation_key=evidence(),
            status="REQUESTED", pickup=required("pickup"), delivery=required("delivery"),
            packages=int(required("packages")), net_weight_kg=required("net_weight_kg"),
            gross_weight_kg=required("gross_weight_kg"), dimensions=required("dimensions"),
            requested_at_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    elif step == "invoice":
        ExecuteDocuments(store).invoice(ids["order"], exporter_legal_id=required("exporter_legal_id"),
            buyer_legal_id=required("buyer_legal_id"), tax_review_ref=required("tax_review_ref"))
    elif step == "packing":
        ExecuteDocuments(store).packing(ids["order"], marks=required("marks"), packages=[{
            key: required(key) for key in ("quantity", "net_weight_kg", "gross_weight_kg", "dimensions")}])
    elif step == "checklist":
        ExecuteDocuments(store).checklist(ids["order"], items=[{
            "name": required("name"), "status": required("status"), "owner": required("owner"),
            "evidence_ref": evidence() if values.get("status") != "UNKNOWN" else ""}])
    elif step.startswith("review-"):
        doc = case["documents"][step.removeprefix("review-")]
        ExecuteDocuments(store).review(doc["document_id"], doc["revision"],
            decision="REVIEW", expected_payload_sha256=doc["payload_sha256"],
            reason=required("reason"))
    else:
        raise ValueError("Bu aşamada işlem yapılamaz")


def _layout(content: str) -> bytes:
    page = ("<!doctype html><html lang='tr'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>Operatör çalışma alanı</title><style>" + STYLE + "</style></head>"
            "<body><header><h1>İhracat Operatör Çalışma Alanı</h1>"
            "<p>Find → Sell → Execute · yerel sentetik deneme</p></header>"
            "<main><div class='banner'><strong>TEST VERİSİ</strong> · Gerçek iletişim, "
            "teklif gönderimi, ERP işlemi veya sevkiyat yapılmaz.</div>" + content
            + "</main></body></html>")
    return page.encode("utf-8")


def _dashboard(app: OperatorWeb) -> bytes:
    cards = []
    for handoff in app.handoffs():
        status = handoff["status"]
        cards.append("<div class='card'><h3>" + _h(handoff["buyer_company_name"])
                     + "</h3><p>Ürün: " + _h(handoff["product_id"])
                     + " · Hedef: " + _h(handoff["market_country"]) + "</p>"
                     + "<p><span class='badge'>" + _h(status) + "</span> "
                     + "<span class='badge'>SENTETİK</span></p>"
                     + "<a class='button' href='/case?id=" + _h(handoff["id"])
                     + "'>Vakayı aç</a></div>")
    demo = ("<div class='card'><h3>İlk deneme</h3><p>Gerçek veri girmeden tüm akışı "
            "denemek için uydurma bir örnek vaka hazırlayın.</p>"
            "<form method='post' action='/demo'><input type='hidden' name='csrf' value='"
            + _h(app.csrf) + "'><label class='check'><input type='checkbox' name='confirm' "
            "value='yes' required>Verilerin tamamen örnek olduğunu anlıyorum.</label>"
            "<button>Örnek vakayı başlat</button></form></div>")
    return _layout("<h2>Find devirleri</h2><p class='muted'>Bir vakayı seçerek ekrandaki "
                   "adımları ilerletin. ID veya JSON girmeniz gerekmez.</p>"
                   + demo + ("".join(cards) if cards else
                      "<div class='card'>Henüz sentetik Find devri yok. Ürün, alıcı uygunluğu "
                      "ve rota kanıtı hazırlığı mevcut Find servislerinde yapılır.</div>"))


def _case_page(app: OperatorWeb, handoff_id: str, error: str = "") -> bytes:
    case = app.chain(handoff_id)
    case["store"] = app.store
    case["data_dir"] = app.config.data_dir
    step = app.step(case)
    handoff = case["handoff"]
    stages = [
        ("Find", handoff["status"], handoff["actor_id"],
         "Kaynak gözlemleri: " + ", ".join(handoff["route_observation_ids"])),
        ("Sell · iletişim", case.get("draft", {}).get("status", "BEKLİYOR"),
         case.get("draft", {}).get("actor_id", "—"),
         "Alıcı: " + str(handoff.get("route_value") or "Gizli / yeniden incele")),
        ("Sell · yanıt / RFQ", case.get("rfq", {}).get("status", "BEKLİYOR"),
         case.get("rfq", {}).get("actor_id", "—"),
         "Yanıt kaynağı: " + str(case.get("inbound", {}).get("raw_ref", "—"))),
        ("Sell · teklif", case.get("quote", {}).get("status", "BEKLİYOR"),
         case.get("quote", {}).get("actor_id", "—"),
         "Fiyat kaynağı: " + str(case.get("quote", {}).get("payload", {}).get("price_source_ref", "—"))),
        ("Execute", case.get("case", {}).get("operations", {}).get("order", {}).get(
            "status", case.get("po", {}).get("status", "BEKLİYOR")),
         case.get("po", {}).get("actor_id", "—"),
         "PO kaynağı: " + str(case.get("po", {}).get("source_ref", "—"))),
    ]
    sidebar = "<div class='panel'><h2>Vaka akışı</h2>"
    for title, status, actor, source in stages:
        sidebar += ("<div class='row'><strong>" + _h(title) + "</strong><br>"
                    "<span class='badge'>" + _h(status) + "</span><br><small>Kaynak: "
                    + _h(source) + "<br>Aktör: " + _h(actor) + "</small></div>")
    sidebar += "</div>"
    details = ("<div class='panel'><a href='/'>← Tüm vakalar</a><h2>"
               + _h(handoff["buyer_company_name"]) + "</h2><p>"
               + _h(case["product"]["sku"]) + " · " + _h(handoff["market_country"])
               + " · <span class='badge'>SENTETİK</span></p>")
    if error:
        details += "<div class='error'>" + _h(error) + "</div>"
    if step == "blocked":
        details += ("<div class='error'>Kaynak, rota, revizyon veya ticari alan yeniden "
                    "inceleme gerektiriyor. İşlem durduruldu; bu ekranda düzeltme "
                    "akışı henüz yok.</div>")
        if case.get("po"):
            details += "<p>PO uyuşmazlıkları: " + _h(", ".join(case["po"]["differences"]) or "Yok") + "</p>"
        if case.get("case"):
            details += "<p>Açık maddeler: " + _h(", ".join(case["case"]["issues"]) or "Yok") + "</p>"
    elif step == "complete":
        summary = case["case"]
        details += ("<h3>Teknik vaka tamamlandı</h3><p>Bu sonuç gerçek sevkiyat "
                    "veya yayımlanmış belge anlamına gelmez.</p><p>Açık maddeler: "
                    + _h(", ".join(summary["issues"]) or "Yok") + "</p>")
        details += ("<h3>Operatör geri bildirimi</h3><form method='post' action='/feedback?id="
                    + _h(handoff_id) + "'><input type='hidden' name='csrf' value='"
                    + _h(app.csrf) + "'>" + _field("friction", "Nerede zorlandınız?", kind="textarea")
                    + _field("suggestion", "Neyi değiştirelim?", kind="textarea")
                    + '<label class="check"><input type="checkbox" name="confirm" value="yes" required>Geri bildirimi kaydet.</label>'
                    + "<button>Geri bildirimi kaydet</button></form>")
    else:
        fields, evidence = _form_for(case, step)
        details += ("<h3>Şimdiki adım: " + _h(TITLES[step]) + "</h3>"
                    "<div class='evidence'>" + _h(evidence) + "</div>"
                    "<form method='post' enctype='multipart/form-data' action='/case?id="
                    + _h(handoff_id) + "'><input type='hidden' name='csrf' value='"
                    + _h(app.csrf) + "'><input type='hidden' name='step' value='"
                    + _h(step) + "'>" + fields + "<button>" + _h(TITLES[step])
                    + "</button></form>")
    details += "</div>"
    return _layout("<div class='grid'>" + sidebar + details + "</div>")


def _parse_form(content_type: str, body: bytes) -> tuple[dict[str, str], dict[str, bytes]]:
    if content_type.startswith("application/x-www-form-urlencoded"):
        data = parse_qs(body.decode("utf-8"), keep_blank_values=True)
        return {key: value[-1] for key, value in data.items()}, {}
    if not content_type.startswith("multipart/form-data;"):
        raise ValueError("Form türü desteklenmiyor")
    message = BytesParser(policy=default).parsebytes(
        b"Content-Type: " + content_type.encode("ascii") + b"\r\n"
        + b"MIME-Version: 1.0\r\n\r\n" + body)
    if not message.is_multipart():
        raise ValueError("Form sınırı geçersiz")
    fields, files = {}, {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name or name in fields or name in files:
            raise ValueError("Tekrarlanan veya eksik form alanı")
        content = part.get_payload(decode=True)
        if part.get_filename() is not None:
            files[name] = content
        else:
            fields[name] = content.decode("utf-8")
    return fields, files


def _save_feedback(app: OperatorWeb, case: dict, values: dict[str, str]) -> None:
    if values.get("confirm") != "yes":
        raise ValueError("Geri bildirim için açık onay gerekli")
    friction, suggestion = (values.get(key, "").strip() for key in ("friction", "suggestion"))
    if not friction or not suggestion or max(len(friction), len(suggestion)) > 2000:
        raise ValueError("Geri bildirim alanlarını doldurun (en fazla 2000 karakter)")
    order_id = case["ids"]["order"]
    actor = app.store._require_access("EDIT_EXECUTE", order_id)
    record = {"order_id": order_id, "friction": friction, "suggestion": suggestion,
              "data_origin": "SYNTHETIC", "actor_id": actor,
              "recorded_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(app.config.data_dir / "operator-feedback.jsonl", flags, 0o600)
    with os.fdopen(descriptor, "a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def make_handler(app: OperatorWeb):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, *, cookie: bool = False,
                  location: str | None = None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'none'; "
                             "style-src 'unsafe-inline'; form-action 'self'; "
                             "base-uri 'none'; frame-ancestors 'none'")
            if cookie:
                self.send_header("Set-Cookie", "ego_session=" + app.session
                                 + "; HttpOnly; SameSite=Strict; Path=/")
            if location:
                self.send_header("Location", location)
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self) -> bool:
            cookies = [part.strip() for part in self.headers.get("Cookie", "").split(";")]
            return ("ego_session=" + app.session) in cookies

        def _case_id(self) -> str:
            parsed = urlsplit(self.path)
            ids = parse_qs(parsed.query).get("id", [])
            if len(ids) != 1 or not ids[0].startswith("FH-"):
                raise ValueError("Geçerli bir vaka seçin")
            return ids[0]

        def do_GET(self) -> None:
            parsed = urlsplit(self.path)
            token = parse_qs(parsed.query).get("token", [])
            if parsed.path == "/" and token == [app.session]:
                self._send(HTTPStatus.SEE_OTHER, b"", cookie=True, location="/")
                return
            if not self._authorized():
                self._send(HTTPStatus.FORBIDDEN, _layout("<h2>Oturum bağlantısı gerekli</h2>"))
                return
            try:
                if parsed.path == "/":
                    body = _dashboard(app)
                elif parsed.path == "/case":
                    body = _case_page(app, self._case_id())
                else:
                    self._send(HTTPStatus.NOT_FOUND, _layout("<h2>Sayfa bulunamadı</h2>"))
                    return
                self._send(HTTPStatus.OK, body)
            except (ValueError, KeyError) as exc:
                self._send(HTTPStatus.BAD_REQUEST, _layout("<h2>Vaka açılamadı</h2><p>"
                                                           + _h(exc) + "</p>"))

        def do_POST(self) -> None:
            parsed = urlsplit(self.path)
            if not self._authorized():
                self._send(HTTPStatus.FORBIDDEN, b"")
                return
            origin = self.headers.get("Origin")
            if origin != f"http://127.0.0.1:{self.server.server_port}":
                self._send(HTTPStatus.FORBIDDEN, b"")
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0 or length > 21_000_000:
                    raise ValueError("Form boyutu geçersiz (en çok 20 MB)")
                values, files = _parse_form(self.headers.get("Content-Type", ""),
                                            self.rfile.read(length))
                if not secrets.compare_digest(values.get("csrf", ""), app.csrf):
                    self._send(HTTPStatus.FORBIDDEN, b"")
                    return
                if parsed.path == "/demo":
                    if values.get("confirm") != "yes":
                        raise ValueError("Örnek vaka için açık onay gerekli")
                    handoff_id = create_demo_handoff(app.store)
                    self._send(HTTPStatus.SEE_OTHER, b"", location="/case?id=" + handoff_id)
                    return
                handoff_id = self._case_id()
                case = app.chain(handoff_id)
                case["store"] = app.store
                case["data_dir"] = app.config.data_dir
                step = app.step(case)
                if parsed.path == "/case" and step == values.get("step"):
                    _execute(case, step, values, files)
                elif parsed.path == "/feedback" and step == "complete":
                    _save_feedback(app, case, values)
                else:
                    raise ValueError("Vaka değişti; sayfayı yenileyip tekrar inceleyin")
                self._send(HTTPStatus.SEE_OTHER, b"", location="/case?id=" + handoff_id)
            except (ValueError, KeyError, TypeError, OSError) as exc:
                try:
                    body = _case_page(app, self._case_id(), str(exc))
                except (ValueError, KeyError):
                    body = _layout("<h2>İşlem durduruldu</h2><p>" + _h(exc) + "</p>")
                self._send(HTTPStatus.BAD_REQUEST, body)

        def log_message(self, format: str, *args: object) -> None:
            # Do not log query tokens, routes, form values or private case data.
            pass

    return Handler


def main() -> None:
    config = AppConfig.from_env()
    app = OperatorWeb(config)
    with ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app)) as server:
        server.daemon_threads = True
        url = f"http://127.0.0.1:{server.server_port}/?token={app.session}"
        print("Yerel sentetik operatör ekranı: " + url, flush=True)
        webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("Operatör ekranı kapatıldı", file=sys.stderr)


if __name__ == "__main__":
    main()

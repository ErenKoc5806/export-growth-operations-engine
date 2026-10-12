"""Button forms for synthetic partial shipments in the local operator screen."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from uuid import uuid4

from pilot_engine.execute_documents import ExecuteDocuments
from pilot_engine.execute_operations import ExecuteOperations
from pilot_engine.shipments import ExecuteShipments


def _evidence() -> str:
    return "SYN-UI-" + uuid4().hex[:16]


def _context(app: object, handoff_id: str, shipment_id: str | None = None) -> dict:
    case = app.chain(handoff_id)
    order_id = case["ids"]["order"]
    if order_id is None or case["case"]["operations"]["order"]["status"] != "LOCAL_ORDER_SYNTHETIC":
        raise ValueError("Onaylı güncel sipariş gerekli")
    if case["case"]["operations"]["manual_erp_handoff"] is None:
        raise ValueError("Önce siparişin manuel ERP devrini kaydedin")
    with closing(app.store._connect()) as db:
        legacy = (db.execute("SELECT 1 FROM execute_document WHERE order_id = ?", (order_id,)).fetchone()
                  or db.execute("""SELECT 1 FROM execute_operation_event WHERE order_id = ?
                    AND kind IN ('FREIGHT_PLAN','BOOKING_CONFIRMATION')""", (order_id,)).fetchone())
    if legacy:
        raise ValueError("Bu siparişte tam sipariş navlun/belge yolu başladı; parçalara bölünemez")
    if shipment_id is not None and shipment_id not in {
            item["id"] for item in case["shipment_summary"]["shipments"]}:
        raise ValueError("Sevkiyat bu siparişe ait değil")
    shipment = ExecuteShipments(app.store).read(shipment_id) if shipment_id else None
    return {"case": case, "order_id": order_id, "shipment": shipment,
            "summary": case["shipment_summary"]}


def _next(context: dict) -> str:
    shipment = context["shipment"]
    if shipment is None:
        return "create"
    if shipment["freight"] is None:
        return "freight"
    packing = shipment["documents"].get("packing")
    if packing is None or packing["status"] in ("REVIEW_REQUIRED", "REVOKED"):
        return "packing"
    if packing["status"] != "REVIEWED_SYNTHETIC":
        return "review-packing"
    if context["case"]["ids"]["export_facts"] is None:
        return "export-facts"
    invoice = shipment["documents"].get("invoice")
    if invoice is None or invoice["status"] in ("REVIEW_REQUIRED", "REVOKED"):
        return "invoice"
    if invoice["status"] != "REVIEWED_SYNTHETIC":
        return "review-invoice"
    if shipment["booking"] is None:
        return "booking"
    if shipment["booking"]["status"] != "CURRENT_SYNTHETIC_OBSERVATION":
        return "blocked"
    return "complete"


def _form(action: str, context: dict) -> tuple[str, str]:
    from pilot_engine.operator_web import _field
    shipment = context["shipment"]
    order = context["case"]["case"]["operations"]["order"]["payload"]
    fields = ""
    evidence = ""
    if action == "create":
        fields += _field("quantity", "Bu sevkiyata ayrılan adet", kind="number")
        evidence = "Miktar hazır toplamı ve kalan sipariş miktarını aşamaz."
    elif action == "freight":
        fields += _field("pickup", "Alım yeri", "Örnek fabrika")
        fields += _field("delivery", "Teslim yeri", order["incoterm_place"])
        fields += _field("net_weight_kg", "Tek paketin net kg", kind="number")
        fields += _field("gross_weight_kg", "Tek paketin brüt kg", kind="number")
        fields += _field("dimensions", "Tek paketin ölçüleri", "40 x 30 x 20 cm")
        evidence = "Sevkiyat için tek paketli sentetik navlun taslağı. Taşıyıcıya gönderilmez."
    elif action == "packing":
        freight = shipment["freight"]["payload"]
        fields += _field("quantity", "Paket içindeki adet", shipment["shipment"]["quantity"], kind="number")
        fields += _field("net_weight_kg", "Net kg", freight["net_weight_kg"], kind="number")
        fields += _field("gross_weight_kg", "Brüt kg", freight["gross_weight_kg"], kind="number")
        fields += _field("dimensions", "Ölçüler", freight["dimensions"])
        fields += _field("marks", "Paket işareti", "SYN-MARK")
        evidence = "Miktar, net/brüt ağırlık ve ölçüler navlun planıyla eşleşmeli."
    elif action == "export-facts":
        for key, label, value in (
                ("hs_code", "Üreticinin incelediği HS kodu", "732690"),
                ("classification_evidence_ref", "Sınıflandırma kanıtı", "SYN-HS-REVIEW"),
                ("origin_country_code", "Menşe ülke kodu", "TR"),
                ("origin_evidence_ref", "Menşe kanıtı", "SYN-ORIGIN-REVIEW"),
                ("manufacturer_review_ref", "Üretici inceleme referansı", "SYN-MFG-REVIEW")):
            fields += _field(key, label, value)
        evidence = "Ürün araştırmasındaki GTİP veya satıcı adresi kanıt yerine geçmez."
    elif action == "invoice":
        for key, label, value in (
                ("exporter_legal_id", "İhracatçı kayıt referansı", "SYN-TR-123"),
                ("buyer_legal_id", "Alıcı kayıt referansı", "SYN-DE-456"),
                ("tax_review_ref", "Vergi incelemesi", "SYN-TAX-REVIEW"),
                ("destination_review_ref", "Hedef ülke incelemesi", "SYN-DE-REVIEW")):
            fields += _field(key, label, value)
        evidence = "Yalnızca bu sevkiyatın miktarı ve onaylı paket bilgileri faturaya geçer."
    elif action.startswith("review-"):
        doc = shipment["documents"][action.removeprefix("review-")]
        fields += _field("reason", "Belge inceleme gerekçesi")
        evidence = (f"Belge: {doc['kind']} · Sürüm: {doc['revision']} · "
                    f"İçerik özeti: {doc['payload_sha256']}\n"
                    f"Miktar: {doc['payload']['quantity']} · "
                    f"Net/Brüt kg: {doc['payload']['net_weight_kg']} / "
                    f"{doc['payload']['gross_weight_kg']}")
        if doc["kind"] == "INVOICE":
            evidence += (f"\nHS/menşe: {doc['payload']['hs_code']} / "
                         f"{doc['payload']['origin_country_code']} · "
                         f"Tutar: {doc['payload']['total']} {doc['payload']['currency']}")
    elif action == "booking":
        fields += _field("outcome", "Taşıyıcı gözlemi", "UNKNOWN", kind="select",
                         choices=("UNKNOWN", "CONFIRMED_SYNTHETIC"))
        fields += _field("confirmation_ref", "Sentetik teyit referansı", "SYN-CARRIER-REF")
        fields += _field("source_ref", "Sentetik kaynak referansı", "SYN-CARRIER-EVIDENCE")
        evidence = "Bu kayıt tek seferlik gözlemdir. Gerçek rezervasyon yapılmaz; UNKNOWN yeniden denenmez."
    return fields, evidence


def shipment_page(app: object, handoff_id: str, shipment_id: str | None = None,
                  error: str = "") -> bytes:
    from pilot_engine.operator_web import _date_field, _field, _h, _layout
    context = _context(app, handoff_id, shipment_id)
    summary = context["summary"]
    operation = context["case"]["case"]["operations"]["readiness"]
    ready = operation["payload"] if operation else None
    ready_text = (f"{ready['status']} · {ready.get('quantity') or '0'} adet"
                  if ready else "Henüz hazırlık teyidi yok")
    base = "/shipments?id=" + _h(handoff_id)
    content = ("<p><a href='/case?id=" + _h(handoff_id) + "'>← Vakaya dön</a> · "
               "<a href='/feedback?id=" + _h(handoff_id) + "'>Deneme notları</a></p>"
               "<h2>Kısmi sevkiyatlar</h2><div class='card'><strong>Sipariş:</strong> "
               + _h(summary["ordered_quantity"]) + " adet · <strong>Ayrılan:</strong> "
               + _h(summary["allocated_quantity"]) + " · <strong>Kalan:</strong> "
               + _h(summary["unallocated_quantity"]) + "<br><strong>Üretici hazırlığı:</strong> "
               + _h(ready_text) + "<br>Gerçek sevk edilen: 0 · yalnızca sentetik kayıt</div>")
    if error:
        content += "<div class='error'>" + _h(error) + "</div>"
    content += ("<div class='card'><h3>Üretici hazırlığını güncelle</h3><p>Hazır miktar "
                "kümülatiftir. Bir parçaya ayrılmış adedin altına düşürülemez.</p>"
                "<form method='post' action='" + base + "'>"
                "<input type='hidden' name='csrf' value='" + _h(app.csrf) + "'>"
                "<input type='hidden' name='action' value='readiness'>"
                + _field("status", "Durum", "PARTIAL", kind="select", choices=("PARTIAL", "READY"))
                + _field("quantity", "Hazır toplam adet", kind="number")
                + _date_field("planned_at_utc", "Planlanan tarih",
                              ready["planned_at_utc"] if ready else
                              datetime.now(timezone.utc).isoformat(timespec="seconds"))
                + _date_field("actual_at_utc", "Fiilî teyit tarihi",
                              datetime.now(timezone.utc).isoformat(timespec="seconds"))
                + _field("reason", "Üretici bilgisi / gerekçe")
                + '<label class="check"><input type="checkbox" name="confirm" value="yes" required>Hazırlık kaydını inceledim.</label>'
                "<button>Hazırlığı kaydet</button></form></div>")
    content += "<div class='grid'><div class='panel'><h3>Sevkiyat parçaları</h3>"
    for index, item in enumerate(summary["shipments"], 1):
        content += ("<div class='row'><a href='" + base + "&shipment=" + _h(item["id"])
                    + "'>Parça " + str(index) + " · " + _h(item["quantity"]) + " adet</a></div>")
    if not summary["shipments"]:
        content += "<p>Henüz parça ayrılmadı.</p>"
    content += "</div><div class='panel'>"
    action = _next(context)
    if shipment_id:
        content += ("<h3>Seçilen sevkiyat · " + _h(context["shipment"]["shipment"]["quantity"])
                    + " adet</h3><p><a href='" + base + "'>Yeni parça aç</a></p>")
        booking = context["shipment"]["booking"]
        if booking:
            content += ("<p>Rezervasyon gözlemi: <span class='badge'>"
                        + _h(booking["status"]) + "</span> · "
                        + _h(booking["payload"]["status"]) + "</p>")
    if action == "blocked":
        content += ("<div class='error'>Kayıtlı taşıyıcı gözleminin dayandığı paketleme veya "
                    "navlun artık güncel değil. İkinci rezervasyon kaydı açılmaz; operatör "
                    "incelemesi gerekli.</div>")
    elif action == "complete":
        content += "<p>Sentetik taslaklar ve gözlem kaydedildi. Gerçek sevkiyat yapılmadı.</p>"
    else:
        titles = {"create": "Yeni sevkiyat parçası aç", "freight": "Navlun taslağı",
                  "packing": "Paketleme taslağı", "review-packing": "Paketlemeyi incele",
                  "export-facts": "Üretici sınıflandırma ve menşe incelemesi",
                  "invoice": "Fatura taslağı", "review-invoice": "Faturayı incele",
                  "booking": "Taşıyıcı gözlemini kaydet"}
        fields, evidence = _form(action, context)
        content += ("<h3>Şimdiki adım: " + _h(titles[action]) + "</h3><div class='evidence'>"
                    + _h(evidence) + "</div><form method='post' action='" + base
                    + ("&shipment=" + _h(shipment_id) if shipment_id else "")
                    + "'><input type='hidden' name='csrf' value='" + _h(app.csrf)
                    + "'><input type='hidden' name='action' value='" + _h(action) + "'>"
                    + fields + '<label class="check"><input type="checkbox" name="confirm" value="yes" required>Alanları ve kanıtı inceledim.</label>'
                    + "<button>" + _h(titles[action]) + "</button></form>")
    content += "</div></div>"
    return _layout(content)


def shipment_execute(app: object, handoff_id: str, shipment_id: str | None,
                     values: dict[str, str]) -> str | None:
    from pilot_engine.operator_web import _utc
    context = _context(app, handoff_id, shipment_id)
    action = values.get("action")
    if values.get("confirm") != "yes" or (action != "readiness" and action != _next(context)):
        raise ValueError("Vaka değişti; sayfayı yenileyip yeniden inceleyin")
    def required(name: str) -> str:
        value = values.get(name, "").strip()
        if not value:
            raise ValueError(f"{name} alanını doldurun")
        return value
    service = ExecuteShipments(app.store)
    order_id = context["order_id"]
    if action == "readiness":
        ExecuteOperations(app.store).readiness(order_id, operation_key=_evidence(),
            status=required("status"), planned_at_utc=_utc(required("planned_at_utc")),
            actual_at_utc=_utc(required("actual_at_utc")), quantity=required("quantity"),
            source_ref=_evidence(), reason=required("reason"), operator_confirmed=True)
    elif action == "create":
        shipment_id = service.create(order_id, operation_key=_evidence(),
                                     quantity=required("quantity"), source_ref=_evidence())
    elif action == "freight":
        service.plan_freight(shipment_id, operation_key=_evidence(), pickup=required("pickup"),
            delivery=required("delivery"), packages=1,
            net_weight_kg=required("net_weight_kg"), gross_weight_kg=required("gross_weight_kg"),
            dimensions=required("dimensions"),
            requested_at_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    elif action == "packing":
        old = context["shipment"]["documents"].get("packing")
        service.packing(shipment_id, packages=[{key: required(key) for key in
            ("quantity", "net_weight_kg", "gross_weight_kg", "dimensions")}],
            marks=required("marks"), document_id=old["document_id"] if old else None,
            expected_revision=old["revision"] if old else 0)
    elif action == "export-facts":
        ExecuteDocuments(app.store).review_export_facts(order_id, hs_code=required("hs_code"),
            classification_evidence_ref=required("classification_evidence_ref"),
            origin_country_code=required("origin_country_code"),
            origin_evidence_ref=required("origin_evidence_ref"),
            manufacturer_review_ref=required("manufacturer_review_ref"))
    elif action == "invoice":
        old = context["shipment"]["documents"].get("invoice")
        service.invoice(shipment_id, exporter_legal_id=required("exporter_legal_id"),
            buyer_legal_id=required("buyer_legal_id"), tax_review_ref=required("tax_review_ref"),
            destination_review_ref=required("destination_review_ref"),
            document_id=old["document_id"] if old else None,
            expected_revision=old["revision"] if old else 0)
    elif action.startswith("review-"):
        doc = context["shipment"]["documents"][action.removeprefix("review-")]
        service.review_document(doc["document_id"], doc["revision"], decision="REVIEW",
            expected_payload_sha256=doc["payload_sha256"], reason=required("reason"))
    elif action == "booking":
        service.confirm_booking(shipment_id, operation_key=_evidence(),
            confirmation_ref=required("confirmation_ref"), source_ref=required("source_ref"),
            confirmed_at_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            outcome=required("outcome"))
    else:
        raise ValueError("Sevkiyat işlemi bilinmiyor")
    return shipment_id

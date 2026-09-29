# FND-001 — Bileşen denetimi (pilot 732690 → Almanya)

Tarih: 29 Eylül 2026. Denetlenen kaynaklar, ana deponun sabitlediği commit'lerdir: [AI-Worker](https://github.com/ErenKoc5806/AI-Worker/tree/4d7cd821ab410dbbd7e98134b4e0909480ecf437) (`4d7cd821`) ve [trade_intelligence](https://github.com/ErenKoc5806/trade_intelligence/tree/a203e0376cdbcc69a6d93132401864aaa470b40d) (`a203e037`). Kodlar yerel olarak ayrı klasörlerde okundu ve dış servis çağrıları yapılmadan kontrol edildi. Kullanıcının iki özel repodaki bulguları proje issue'larına kaydetme onayı vardır.

## Karar özeti

İlk pilot için **trade_intelligence, Find ve Sell tarafındaki en güçlü başlangıç noktası**. Veriye erişim, araştırma, temas kaydı, taslak ve insan onaylı gönderim için çalışan yerel uygulama ve HTTP testleri var. Bunun ticari mantığı ağırlıkla fırsat/aracılık hazırlığına odaklı; üreticinin fiyat listesi, müşteri RFQ'su, satış siparişi ve sevkiyat belgelerine doğrudan karşılık gelmiyor.

**AI-Worker, Sell/Execute için yeniden kullanılabilecek modeller ve işlevler içeriyor**, özellikle inquiry → quotation, müşteri PO'su → ERP veri eşlemesi ve sevkiyat planı. Fakat bütün uygulamanın çalıştığı kanıtlanmadı: yürütme kayıt modeli ve deposu boş; ilgili modül içe aktarılamıyor, ayrıca `app.main` temiz bir ortamda döngüsel import nedeniyle başlamıyor. Canias/Outlook adaptörleri bağlantısız; varsayılan fiyat ve ERP/posta sağlayıcıları mock. Bileşeni olduğu gibi pilotun omurgası yapmak yerine, dar ve test edilmiş işlevlerini adapte etmek daha güvenli.

## Gözlenen yüzeyler

| Alan | trade_intelligence | AI-Worker | Pilot kararı |
| --- | --- | --- | --- |
| Giriş | `app.api:app` FastAPI; `app.market_cli`, araştırma ve bakım CLI'ları | `python -m app.main` PDF inbox/router; modüler worker ve workflow tanımları | Kullanıcı akışı ana ürün sınırında tanımlanacak; modülleri doğrudan aynı Python import yoluna koymayın. |
| Find | Comtrade sağlayıcısı, HS6 dünya/ülke verisi, karşı taraf/iletişim araştırması, kaynak izleri | Pazar ve lead yapıları var; doğrulanmış uçtan uca keşif kanıtı yok | 732690/DE için veri ve iletişim araştırmasını yeniden kullan; gerçek ürün uygunluğunu ayrıca doğrula. |
| Sell | Opportunity workspace, kontak, taslak, yanıt, maliyet kaydı, Microsoft Graph gönderim onayı | Inquiry, quotation, CRM/follow-up, mock pricing ve mock mail | Ticari kayıt ve onay akışı yeniden kullanılabilir; üretici fiyatı/teklif revizyonu için yeni sözleşme ve test gerekir. |
| Execute | Son insan incelemesine kadar ticari hazırlık; sürüm notu sipariş ve sevkiyatı dışlıyor | PO PDF/AI ayrıştırma, PO→ERP eşleme, sevkiyat modelleri/workflow; gerçek Canias adaptörü aktif değil | Önce yerel satış siparişi, ardından taslak fatura ve packing list üretimini geliştir; ERP ve booking bağımsız adaptör olsun. |
| Kalıcılık | `SQLiteJsonStore` ve JSON uyumluluk katmanı; testlerde yeniden yükleme/backup var | SQLite repository sınıfları ve veri klasörleri; tüm akışlar doğrulanmadı | Tek pilot kayıt kimliği ve hangi servis hangi verinin sahibi tanımlanmalı. |
| Dış servis | Comtrade/OpenAI isteğe bağlı; Graph transport kodu mevcut, gerçek gönderim denenmedi | OpenAI PDF ayrıştırma; Outlook/Canias `NOT_CONFIGURED`/hata döndürüyor | Sahte veri ve mock sağlayıcıyla kabul; canlı servis kanıtı ayrı pilot kapısı. |

## Doğrulama kanıtı ve sınırlar

- trade_intelligence: yalıtılmış Python 3.12 ortamında, ağ bağlantısı kapalı tutularak `pytest -q tests` çalıştı: **206 passed, 7 subtests passed**. Testler HTTP çalışma alanı, yerel araştırma ve onay akışını da kapsıyor; gerçek Comtrade, OpenAI, Graph, alıcı e-postası veya ERP bağlantısını doğrulamıyor.
- AI-Worker: `pytest -q tests` sonucu **no tests ran**; mevcut `tests/test_mail_worker.py` çıktı yazdıran örnek, assertion içermiyor. Sentetik PO'nun `map_parsed_po_to_erp` ile eşlenmesi ve eksik müşteri bilgisinin reddi ad hoc yerel kontrolde geçti.
- `core_platform.reliability.execution_service` import'u, `ExecutionRecord` tanımı boş `models/execution.py` dosyasında bulunmadığı için hata veriyor. `repositories/execution_repository.py` de boş.
- Temiz ortamda proxy değişkenleri kaldırılarak `app.main` import'u denendi; ERPService çevresinde döngüsel import hatası verdi. Bu uygulama giriş noktasının çalışır olduğunu varsayamayız.
- `services/canias_erp_service.py` her zaman `NOT_CONFIGURED`/başarısız sonuç döndürüyor; `services/outlook_mail_provider_service.py` gönderimde hata fırlatıyor. `MockPricingService` varsayılan 100 EUR değerini kullanıyor; gerçek müşteri fiyatı değildir.
- trade_intelligence Graph transport kodu var. Onay kimliği ve içerik hash'i ile tek kullanımlık `SEND` akışı test ediliyor; bağlantı sonucu belirsizse manuel kontrol durumuna alınıyor. Testte kullanılan gönderici sahte; gerçek yetki ve teslimat gözlemlenmedi.
- İncelenen API'de görünür bir authentication/authorization bağımlılığı yok. Üretici verisi ve gerçek gönderim bağlanmadan önce FND erişim kontrolü işinde ayrıca ele alınmalı.

## Pilotun eksikleri ve sonraki işler

1. **Ortak sözleşme:** `product` (gerçek ürün açıklaması ve GTIP 732690), `market=DE`, `company`, `contact` (kaynak/kanıt), `opportunity`, `RFQ`, `quotation` (sürüm ve onay), `customer_PO`, `sales_order`, `shipment`, `document` için kimlik ve durum geçişleri. GTIP tek başına ürün spesifikasyonu değildir.
2. **Paket sınırı:** Her iki repoda da `app` Python paketi var. Aynı interpreter içinde import ederek birleştirmek ad çakışmasına yol açar. Pilot için Trade Intelligence'ı bağımsız servis olarak tutup ana ürüne açık API/sözleşmeyle bağlama; AI-Worker'dan yalnızca seçilen fonksiyonları paketleyerek adapte etme kararı FND-002'de verilmeli.
3. **Find → Sell geçişi:** Doğrulanmış şirket/iletişim, kaynak tarihi, ürün uygunluğu ve operatör onayı tek fırsat kimliğiyle RFQ/teklif sürecine geçmeli. Mevcut brokerage/maliyet modelini otomatik üretici satış fiyatı saymayın.
4. **Sell → Execute geçişi:** Onaylı teklif ve gerçek/sentetik PO eşleştirmesi, tutar/para birimi/miktar mutabakatı, tekil satış siparişi ve hata kuyruğu gerekir. AI-Worker PO eşlemesi bunun bir parçası; ERP adaptörü hazır değil.
5. **Belge çıktısı:** Üreticinin satış siparişine bağlı taslak ticari fatura ve packing list oluşturma/inceleme akışı repolarda gözlenmedi. Bu, pilotun yeni geliştirme parçası.
6. **Depo temizliği:** AI-Worker'ın takip edilen `.venv` klasörü ve boş README/.env.example dosyaları; trade_intelligence'ın kökteki üretilmiş JSON, DB ve lock dosyaları. Veri/fixture ayrımı yapılmadan silme veya geçmiş yeniden yazma yapılmamalı.
7. **Canlı pilot kapıları:** Ürün/SKU, fiyat sahibi, operatör, veri kaynağı, yetki modeli, kontrollü test posta kutusu ve ERP dışa aktarım biçimi doğrulanmalı. Gerçek gönderim, ERP siparişi veya sevkiyat testleri burada yapılmadı.

Bu denetim teknik yeniden kullanım kararını destekler; FND-017'nin ürün/süreç onayını ve FND-002 mimari kararını otomatik olarak tamamlamaz.

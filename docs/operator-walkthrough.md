# Sentetik operatör denemesi (#47)

Bu denemeyi yazılım geliştirme deneyimi olmayan bir dış ticaret veya satış
operatörü yapmalı. Gerçek müşteri, fiyat veya belge kullanılmaz. Yönetici,
`operator-web.md` içindeki yerel uygulamayı açar ve yalnızca tarayıcı
bağlantısını operatöre verir. Windows PowerShell'de Python 3.12 ile sentetik
deneme açılabilir; WSL gerekmez. Denemenin çıktısını gerçek pilot verisi veya
gerçek sevkiyat saymayın.

1. Operatör **Örnek vakayı başlat** ile uydurma vakayı açar. Yardımcı kişi
   işlem alanlarını doldurmaz; yalnızca güvenlik ve kurulum sorunlarında yardım
   eder. Başlangıç saatini not edin.
2. Find devrindeki ürün/alıcı/kanalı inceleyip Sell taslağına, sentetik
   yakalamaya, örnek RFQ'ya ve teklif onayına ilerler. Alıcı, içerik ve revizyonu
   kendi sözleriyle açıklayıp açıklayamadığını not edin.
3. PO formunda önce örnek dosyaya uygun bir alanı kasıtlı yanlış girer; PO
   karşılaştırmasını okuyarak **PO girişini düzelt** yolundan aynı dosyayı
   yeniden seçip yeni sürümü oluşturur. Ardından PO'yu onaylar. Başka bir
   denemede alıcı adı veya daha düşük miktarı, toplamı da uyumlu olacak şekilde
   girip her farkı ayrı inceler. Gerçek fark ile yanlış veri girişini
   karıştırıp karıştırmadığını not edin.
4. ERP manuel devri ve üreticiden `PARTIAL` hazırlık bilgisini kaydeder;
   **Kısmi sevkiyatlar** ekranında ilk parçayı açar. Navlun, paketleme ve
   fatura taslaklarını inceler. Kalan miktarı ve `real_shipped_quantity: 0`
   bilgisini bulabilmeli. İkinci parça için hazır toplamı yükseltir.
5. **Deneme notları** ekranına kendi süresini, düzelttiği giriş sayısını,
   duraksadığı adımı ve önerisini yazar. Yardımcı kişi de ayrı olarak ekranda
   takıldığı alanları not eder; operatörün yerine başarı onayı vermez.

Başarı ölçütü: ID, JSON veya Python girmeden akışı takip edebilmek; mock
gönderim, taslak belge ve ERP devri ile gerçek dış işlem arasındaki farkı
anlamak. Kaydedilen süre ve düzeltme sayısı öz bildirime dayanır. Bu doküman
deneme sonucu değildir; gerçek insan gözlemi yapılınca #47'deki açık
sürtünme noktaları güncellenir.

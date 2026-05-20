<!-- ai-controller -->
<!-- kurulum dosyasi ve sudo chmod,dosyalari yerlestirme klasor olusturma ve izinler -->
<!-- cli icin resim -->
<!-- ram tuketimi gibi bilgiler-->
<!-- hangi pdf sayfalari gidecek,thinking aninda siraya prompt koyma ve onun gozukmesi,biz chat1deyken chat2 den cevap gelirse kullaniciya gosterme -->

## 👀 capture-ai Overview

[![License](https://img.shields.io/github/license/FURK4NGG/capture-ai)](https://github.com/FURK4NGG/capture-ai/blob/main/LICENSE)

Capture AI is not just a simple chat application.

It is a hybrid AI platform that combines:

💬 Conversational AI  
📄 Document processing & editing  
⚙️ Intelligent pipelines  
💻 Coding workflows  
🎨 Image generation & visual AI tasks  

All in a single system.

Unlike traditional chat tools, Capture AI can understand, transform, and generate real files — not just text.

From a single prompt, it can:

PDF → Extract → Transform → Rebuild → Download

It supports both online models (OpenRouter) and local AI providers, giving full control over performance, privacy, and behavior.

Everything in Capture AI is fully open-source and designed to be transparent, customizable, and user-controlled.

Capture AI combines the flexibility of coding-focused AI tools with everyday AI workflows in a single application.


[![Demo Video](https://github.com/FURK4NGG/capture-ai/blob/main/{}/capture-ai.png)](https://github.com/FURK4NGG/capture-ai/blob/main/{}/capture-ai.mp4)


## 🚀 Features
- [x] Compatible with Linux(Arch,Debian/Ubuntu) devices
- [x] Model management per chat
- [x] Per-chat conversational RAG system with enable/disable support(short-term memory + summary memory + embedding-based retrieval + code-aware context)
- [x] Run local AI providers (Ollama, LM Studio, vLLM, etc.)
- [x] Shows how many tokens are consumed for each message
- [x] AI can read, analyze, modify your documents (within permission)
- [x] AI-generated files are automatically created, saved, and shown with download buttons
- [x] Supports PDF, DOCX, XLSX, TXT, and MD (read & generate)
- [x] Editable file permission system (safe file editing control)
- [x] Unlimited reference tree support
- [x] Image generation and image-based workflows
- [x] Regenerate
- [x] Modular prompt system (Prompt Chooser)
- [x] Allows both online and local AI models to access current web search results using Tavily while keeping searches permission-based for privacy and user control
- [x] Enter input with your voice -Speech to text(online or local)-
- [x] Copyable code blocks
- [x] Control via Terminal (excluding STT and some UI features)
- [x] Customize how the AI responds
- [x] Real-time independent multi-chat requests and streaming
- [x] Adding documents via drag & drop
- [x] Lazy Loading, Chat Loading System (Loads latest messages first,older messages load on scroll)
- [x] Change UI colors
- [x] Dark/Light themes
- [x] Keep all your chats in your machine
- [x] Just one configure file
- [x] Streaming response system (real-time output)
- [x] Easy access with keyboard shortcuts
- [x] Language support [Turkish, English]*You can easily create your own language file*
- [x] Caching the language file to avoid repeated file reads
- [ ] Compatible with macOS, Windows
- [ ] Audio,Video,Gif Editing
- [ ] Humanize Mode

## 📦 Setup
1. `Go to the`[`Open Router`](https://openrouter.ai/)`and create your own api key`
2. `Go to the`[`Tavily`](https://app.tavily.com/home)`and create your own api key`
<details>
<summary>?</summary>
      OpenRouter: Provides access to online AI models through a single API.*Some models may require paid usage depending on the provider.*<br><br>
      Tavily: Provides web search results and current online information for AI models.*The free plan includes up to 1000 searches per month in basic search mode.*
</details>

<details>
<summary>3. Make sure you place your files in the following directories.</summary>
   ~/capture-ai/ui.py<br>
   ~/capture-ai/ai.py<br>
   ~/capture-ai/cli.py<br>
   ~/capture-ai/capture-ai.sh<br>
   ~/capture-ai/memory.py<br>
   ~/capture-ai/language/en.json<br>
   ~/capture-ai/language/tr.json<br>
   ~/.config/capture-ai/config.json<br>
   ~/.config/capture-ai/requirements.txt<br>
   ~/.config/scripts/screenprint.sh
</details>
<details>
<summary>4. Download Packages</summary>

   <details>
   <summary>Arch Packages</summary>


   ```
   cd ~/.config/capture-ai/
   ```
   🧩 Core System & Python
   ```
   sudo pacman -S --needed python python-virtualenv git
   ```
   Environment
   ```
   python -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```
   <br><br>
   🖥️ GTK4 UI Dependencies
   ```
   sudo pacman -S --needed gtk4 libadwaita python-gobject gobject-introspection
   ```  
   <br><br>
   🎨 Rendering & Graphics
   ```
   sudo pacman -S --needed cairo pango gdk-pixbuf2
   ```
   <br><br>
   🌐 Network & Runtime
   ```
   sudo pacman -S --needed wget
   ```
   <br><br>
   📄 File Processing (DOCX / XLSX / PDF)
   ```
   sudo pacman -S --needed poppler libreoffice ttf-dejavu
   ```
   <br><br>
   🎧 Audio / Voice  
   
   🎙️ voice record -> 📄 WAV file -> 🧠 whisper-cli -> ✍ Text input  
   <br>
   🎙️ Offline Voice Input,voice record (Speech → Text)
   ```
   sudo pacman -S --needed pipewire wireplumber pipewire-audio pipewire-pulse libpulse alsa-utils
   ```
   <br><br>
   📄 Build Offline Speech to Text
    
   ```
   sudo pacman -S --needed cmake make gcc
   ```

   Packages Check
   ```
   command -v pw-record || echo "pw-record not found"  
   command -v arecord  || echo "arecord not found"  
   ```
   <br><br>
   🧠 Install whisper.cpp (Offline Speech Recognition Engine)  
   
   ```
   git clone https://github.com/ggml-org/whisper.cpp.git ~/whisper.cpp
   cd whisper.cpp  
   cmake -B build  
   cmake --build build -j --config Release
   ```
   
   >ls build/bin  
   #Should see these -> ... whisper-cli, main, whisper-server, ...

   <br><br>
   Download 'Tiny' Model  
   ```
   mkdir -p ~/.local/share/whisper  
   wget -O ~/.local/share/whisper/ggml-tiny.bin https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-tiny.bin
   ```
   
   Packages Check  
   ```
   ls -l ~/whisper.cpp/build/bin/whisper-cli  
   ls -lh ~/.local/share/whisper/ggml-tiny.bin
   ```
   
   Manual Test  
   ```
   ~/whisper.cpp/build/bin/whisper-cli \
     -m ~/.local/share/whisper/ggml-tiny.bin \
     -f /tmp/capture-ai-mic-20260228-205753.wav \
     -l tr
   ```
   <br><br>
   📸 Screenshot
   ```
   sudo pacman -S --needed grim slurp mako libnotify
   ```
   <br><br>
   🧰 System Utilities
   ```
   sudo pacman -S --needed glib2 xdg-utils noto-fonts-emoji
   ```
   </details>












   <details>
   <summary>Debian/Ubuntu/Raspberry Pi OS</summary>

      
   ```
   cd ~/.config/capture-ai/
   ```
   🧩 Core System & Python
   ```
   sudo apt update
   sudo apt install -y python3 python3-venv git
   ```
   Environment
   ```
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```
   <br><br>
   🖥️ GTK4 UI Dependencies
   ```
   sudo apt install -y python3-gi python3-gi-cairo gobject-introspection gir1.2-gtk-4.0 gir1.2-adw-1 libgtk-4-1 libadwaita-1-0
   ```
   <br><br>
   🎨 Rendering & Graphics
   ```
   sudo apt install -y libcairo2 libpango-1.0-0 gir1.2-pango-1.0 gir1.2-gdkpixbuf-2.0
   ```
   <br><br>
   🌐 Network & Runtime
   ```
   sudo apt install -y wget
   ```
   <br><br>
   📄 File Processing (DOCX / XLSX / PDF)
   ```
   sudo apt install -y poppler-utils libreoffice fonts-dejavu
   ```
   <br><br>
   🎧 Audio / Voice  
   
   🎙️ voice record -> 📄 WAV file -> 🧠 whisper-cli -> ✍ Text input  
   <br>
   🎙️ Offline Voice Input,voice record (Speech → Text)
   ```
   sudo apt install -y pipewire wireplumber pipewire-pulse libpipewire-0.3-0 libspa-0.2-modules alsa-utils pulseaudio-utils libpulse0
   ```
   <br><br>
   📄 Build Offline Speech to Text
    
   ```
   sudo apt install -y cmake make gcc
   ```

   Packages Check
   ```
   command -v pw-record || echo "pw-record not found"
   command -v arecord || echo "arecord not found"
   ```
   <br><br>
   🧠 Install whisper.cpp (Offline Speech Recognition Engine)  
   
   ```
   git clone https://github.com/ggml-org/whisper.cpp.git ~/whisper.cpp
   cd ~/whisper.cpp
   cmake -B build
   cmake --build build -j --config Release
   ```
   
   >ls build/bin  
   #Should see these -> ... whisper-cli, main, whisper-server, ...

   <br><br>
   Download 'Tiny' Model  
   ```
   mkdir -p ~/.local/share/whisper
   wget -O ~/.local/share/whisper/ggml-tiny.bin https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-tiny.bin
   ```
   
   Packages Check  
   ```
   ls -l ~/whisper.cpp/build/bin/whisper-cli
   ls -lh ~/.local/share/whisper/ggml-tiny.bin
   ```
   
   Manual Test  
   ```
   ~/whisper.cpp/build/bin/whisper-cli \
     -m ~/.local/share/whisper/ggml-tiny.bin \
     -f /tmp/capture-ai-mic-20260228-205753.wav \
     -l tr
   ```
   <br><br>
   📸 Screenshot
   ```
   sudo apt install -y grim slurp scrot dunst libnotify-bin
   ```
   <br><br>
   🧰 System Utilities
   ```
   sudo apt install -y xdg-utils xdg-desktop-portal xdg-desktop-portal-gtk fonts-noto-color-emoji
   ```
   </details>

</details>
   



## 🎉 Run
```
bash capture-ai.sh (image,text,cli)  
```


## ------------------HYPRLAND.CONF------------------
$capture-ai = /home/$USER/capture-ai/capture-ai.sh  

bind = $mainMod SHIFT, Q, exec, $capture-ai image  
bind = $mainMod, Q, exec, $capture-ai text  


<br><br>

 
## 🔎 ALL APP FEATURES
<details>
<summary>For Nerds</summary>


1. Chat management  
Create, switch, delete, rename, pin/unpin chats. Chats are sorted with pinned chats first, then by last modified time.

2. Local chat storage  
All chats are stored locally on the machine under the app cache directory.

3. Section-based chat loading  
Chats use lazy loading. Only the latest 10 messages are loaded first, and older messages load while scrolling up.

4. Persistent configuration  
Uses one config.json file for settings such as theme, models, pinned chats, last chat, STT, RAG, colors, local providers, and language.

5. Per-chat model management  
Each chat can have its own active AI model. Recently selected models move to the top of the list.

6. Online and local model support  
Supports OpenRouter models and local API-based text models such as Ollama.

7. Local provider settings  
Local providers can store base URL, startup command, stop command, system prompt, and model parameters.

8. Sidebar UI  
Collapsible sidebar with separate Chats and AI Models sections.

9. Context mode switch  
Each chat can switch between Direct mode and RAG mode.

10. Per-chat conversational RAG  
Supports short-term memory, summary memory, simple retrieval, and code-aware context.

11. Reference tree support  
Selected references can expand recursively so previous context is not lost.

12. Message selection mode  
Users can select one or multiple messages, clear selection, copy, regenerate, or use them as references.

13. Regenerate  
Regenerates from selected user or assistant messages while keeping reference context.

14. Copyable code blocks  
Copy-marked content is rendered as a code-style block with a copy button.

15. Image generation and image handling  
Supports image generation, image previews, cached generated images, and image attachments.

16. Document support  
Supports PDF, DOCX, XLSX, TXT, and MD file creation/output. Generated files are shown with downloadable buttons.

17. PDF handling  
If a PDF contains text, it is sent as text content. If it has little/no text, the first pages are converted to PNG images and sent as image_url.

18. Document editing behavior  
AI can read, analyze, summarize, rewrite, and generate edited document outputs when permission is enabled.

19. File create protocol  
DOCX, XLSX, PDF, TXT, and MD outputs can be generated from AI responses and returned as downloadable files.

20. XLSX support  
Can create new XLSX tables and filter existing XLSX files with supported operations.

21. Drag & drop attachments  
Files and images can be added via drag & drop or file picker.

22. Editable file permission  
Attached files can be toggled editable. The AI only modifies files when permission is enabled.

23. Voice-to-text input  
Supports microphone recording and transcription with online or local STT.

24. STT countdown recording  
Voice recording stop automatically after a configured timeout. When enabled, the remaining seconds are shown during recording.

25. STT silence auto-stop  
Voice recording stop automatically when silence is detected. The silence duration can be configured from STT settings.

26. Offline STT  
Uses whisper.cpp with configured binary and model paths.

27. Online STT  
Uses OpenRouter audio-capable models and sends WAV audio as input_audio.

28. Token usage display  
Shows input, output, and total token usage for each response when enabled.

29. Token price display  
Can estimate message cost using a configurable token price value.

30. Theme system  
Supports dark/light theme and custom UI colors.

31. Language system  
Supports external language files such as Turkish and English, with cached language loading.

32. Prompt chooser  
Prompt behavior blocks can be enabled/disabled, such as copyable, apply, PDF visual edit, file creation, structured output, and code mode.

33. Terminal control  
The project also supports terminal usage, excluding STT and some GUI-only features.

34. Linux compatibility  
Designed for Linux devices, including Arch and Debian/Ubuntu-based systems.

35. Streaming response system  
AI responses are streamed in real-time. The assistant message appears gradually as it is being generated instead of waiting for the full response.

36. Advanced PDF processing pipeline  
      pdf_text  
            → extract text from the PDF  
            → AI generates DOCX  
            → app converts DOCX back to PDF  

      pdf_image
            → if PDF Image mode is selected, convert PDF pages to PNG  
            → AI analyzes the image or returns a PNG  
            → if a PNG is returned, the app converts it into a PDF  

      pdf_image + mixed/image block  
            → extract image blocks from the PDF  
            → AI returns edited PNG  
            → app places the new image back into the original PDF at the same position  

      pdf_text_image  
            → extract layout as JSON + extract images as PNG  
            → AI returns text_replacements JSON (and optionally images)  
            → app rebuilds the PDF using the original layout with updated text and images  

38. Generated files system  
AI can return generated files (PDF, DOCX, XLSX, etc.), which are automatically saved in the app cache and displayed in chat with download buttons.

39. Structured file generation protocol  
AI responses can include structured file_create blocks, allowing the app to generate real files programmatically without manual parsing.

40. Editable file safety system  
Files can be marked as editable or read-only. AI is strictly prevented from modifying files unless explicit permission is enabled.

41. Smart PDF type detection  
Automatically detects whether a PDF is text-based, image-based, or mixed, and applies the appropriate processing pipeline.

42. Mixed PDF layout reconstruction  
For PDFs containing both text and images, the app extracts layout structure and rebuilds the document after AI modifications.

43. Image-to-PDF auto conversion  
If the AI returns image outputs (e.g., PNG), the app automatically converts them into PDF format when needed.

44. Generated image caching  
All generated images are cached locally and can be reused without re-generation.

45. AI-returned file handling  
Supports file outputs returned as base64 or URLs and converts them into downloadable files automatically.

46. Modular prompt system  
System prompts are divided into selectable blocks, allowing dynamic control over AI behavior without modifying core logic.

47. Local provider startup automation  
Local AI providers can be automatically started or stopped using configured commands.

48. Chat-aware context building  
The system intelligently builds context using recent messages, summaries, code context, and relevant memory chunks.

49. Real-time independent multi-chat streaming  
Multiple chats can send AI requests simultaneously. Each chat processes, streams, and updates responses independently in real time.

50. Integrated web search  
Supports AI-powered web search and current online information retrieval using Tavily.  
User -> AI -> if required(User Permission) -> Tavily -> AI -> User

51. CLI dynamic yes/no input  
CLI prompts accept y, yes, n, no, the localized o_Yes / o_No values, and their first letters.
</details>

<details>
<summary>Bilgi Hastaları için</summary>

   
1. Chat yönetimi  
Chat oluşturma, değiştirme, silme, yeniden adlandırma, sabitleme/sabitten çıkarma. Chatler önce sabitlenenler, sonra son değiştirilme zamanına göre sıralanır.

2. Yerel chat saklama  
Tüm chatler uygulamanın cache klasörü altında yerel olarak saklanır.

3. Bölümlü chat yükleme  
Chatler lazy loading kullanır. İlk olarak sadece son 10 mesaj yüklenir, yukarı kaydırıldıkça eski mesajlar yüklenir.

4. Kalıcı yapılandırma  
Tema, modeller, sabit chatler, son chat, STT, RAG, renkler, local provider’lar ve dil ayarları tek bir config.json dosyasında tutulur.

5. Chat başına model yönetimi  
Her chat kendi aktif AI modeline sahip olabilir. En son seçilen modeller listenin en üstüne alınır.

6. Online ve local model desteği  
OpenRouter modelleri ve Ollama gibi local API tabanlı modeller desteklenir.

7. Local provider ayarları  
Base URL, başlatma komutu, durdurma komutu, system prompt ve model parametreleri tanımlanabilir.

8. Sidebar arayüzü  
Açılıp kapanabilen sidebar içinde ayrı Chat ve AI Model listeleri bulunur.

9. Context modu geçişi  
Her chat Direct mode ve RAG mode arasında geçiş yapabilir.

10. Chat başına RAG sistemi  
Kısa süreli hafıza, özet hafıza, basit retrieval ve kod farkındalıklı context desteği vardır.

11. Reference tree desteği  
Seçilen referanslar recursive olarak genişletilir, böylece context kaybı yaşanmaz.

12. Mesaj seçme modu  
Kullanıcı bir veya birden fazla mesaj seçebilir, temizleyebilir, kopyalayabilir, yeniden oluşturabilir veya referans olarak kullanabilir.

13. Regenerate (yeniden oluşturma)  
Seçilen user veya bot mesajlarından yeniden üretim yapılır ve referans context korunur.

14. Kopyalanabilir kod blokları  
Copy ile işaretlenen içerikler, kopyalama butonu olan kod blokları olarak gösterilir.

15. Görsel üretimi ve yönetimi  
Görsel üretimi, önizleme, cache’lenmiş görseller ve image attachment desteği bulunur.

16. Doküman desteği  
PDF, DOCX, XLSX, TXT ve MD dosyaları oluşturma ve çıktı alma desteklenir. Üretilen dosyalar indirilebilir olarak sunulur.

17. PDF işleme  
PDF metin içeriyorsa text olarak gönderilir. Metin yoksa ilk sayfalar PNG’ye çevrilerek image_url olarak gönderilir.

18. Doküman düzenleme davranışı  
AI, izin verildiğinde dosyaları okuyabilir, analiz edebilir, özetleyebilir, yeniden yazabilir ve düzenlenmiş çıktı oluşturabilir.

19. Dosya oluşturma protokolü  
DOCX, XLSX, PDF, TXT ve MD dosyaları AI çıktısından oluşturulabilir ve indirilebilir olarak sunulur.

20. XLSX desteği  
Yeni tablolar oluşturabilir ve mevcut XLSX dosyaları filtreleyebilir.

21. Drag & drop dosya ekleme  
Dosyalar ve görseller sürükle-bırak veya dosya seçici ile eklenebilir.

22. Düzenlenebilir dosya izni  
Eklenen dosyalar editable olarak işaretlenebilir. AI sadece izin verildiğinde değişiklik yapar.

23. Voice-to-text girişi  
Mikrofon ile kayıt ve online/local STT ile metne çevirme desteklenir.

24. STT geri sayımlı kayıt  
Ses kaydı, ayarlanan süre dolunca otomatik olarak durur. Etkinleştirildiğinde kayıt sırasında kalan süre gösterilir.

25. STT sessizlikte otomatik durdurma  
Ses kaydı, sessizlik algılandığında otomatik olarak durur. Sessizlik süresi STT ayarlarından değiştirilebilir.

26. Offline STT  
whisper.cpp kullanarak yerel ses tanıma yapılır.

27. Online STT  
OpenRouter üzerinden ses modeli kullanılarak WAV verisi input_audio olarak gönderilir.

28. Token kullanım gösterimi  
Her mesaj için input, output ve toplam token kullanımı gösterilebilir.

29. Token maliyet hesaplama  
Mesaj maliyeti, ayarlanabilir token fiyatına göre tahmin edilebilir.

30. Tema sistemi  
Dark/light tema ve özelleştirilebilir UI renkleri desteklenir.

31. Dil sistemi  
Türkçe ve İngilizce gibi dış dil dosyaları desteklenir ve cache’lenerek performans artırılır.

32. Prompt chooser  
copyable, apply, PDF edit, file create, structured output ve code gibi prompt blokları açılıp kapatılabilir.

33. Terminal kontrolü  
STT ve bazı UI özellikleri hariç terminal üzerinden kullanım desteklenir.

34. Linux uyumluluğu  
Arch ve Debian/Ubuntu dahil Linux sistemler için tasarlanmıştır.

35. Streaming yanıt sistemi  
AI yanıtları gerçek zamanlı olarak akış halinde gösterilir. Asistan mesajı, tamamının oluşmasını beklemek yerine yazılırken kademeli olarak ekranda görünür.

36. Gelişmiş PDF işleme pipeline’ı  
      pdf_text  
            → PDF’ten metin çıkarılır  
            → AI DOCX üretir  
            → uygulama DOCX’i tekrar PDF’e çevirir  

      pdf_image  
            → PDF Image modu seçiliyse sayfalar PNG’ye çevrilir  
            → AI görseli analiz eder veya PNG döndürür  
            → PNG dönerse uygulama bunu PDF’e çevirir  

      pdf_image + mixed/image block  
            → PDF’ten görsel bloklar çıkarılır  
            → AI düzenlenmiş PNG döndürür  
            → uygulama yeni görseli PDF içinde aynı konuma yerleştirir  

      pdf_text_image  
            → layout JSON olarak çıkarılır + görseller PNG olarak alınır  
            → AI text_replacements JSON (ve opsiyonel görseller) döndürür  
            → uygulama orijinal layout’u kullanarak PDF’i yeniden oluşturur  

38. Üretilen dosya sistemi  
AI tarafından oluşturulan dosyalar (PDF, DOCX, XLSX vb.) otomatik olarak uygulama cache dizinine kaydedilir ve sohbet içinde indirme butonlarıyla gösterilir.

39. Yapılandırılmış dosya üretim protokolü  
AI yanıtları, manuel parse gerektirmeden doğrudan dosya üretimini sağlayan yapılandırılmış file_create blokları içerebilir.

40. Düzenlenebilir dosya güvenlik sistemi  
Dosyalar düzenlenebilir veya salt okunur olarak işaretlenebilir. Açık izin verilmeden AI’ın dosyaları değiştirmesi kesin olarak engellenir.

41. Akıllı PDF türü tespiti  
PDF’in metin tabanlı, görsel tabanlı veya karışık olup olmadığı otomatik olarak tespit edilir ve uygun işleme pipeline’ı uygulanır.

42. Karışık PDF layout yeniden oluşturma  
Hem metin hem görsel içeren PDF’lerde, layout yapısı çıkarılır ve AI düzenlemelerinden sonra belge yeniden oluşturulur.

43. Görselden PDF’e otomatik dönüşüm  
AI görsel (örneğin PNG) çıktısı verdiğinde, uygulama bunu otomatik olarak PDF formatına dönüştürür.

44. Üretilen görsel cache sistemi  
Oluşturulan tüm görseller yerel olarak cache’lenir ve tekrar üretmeye gerek kalmadan yeniden kullanılabilir.

45. AI tarafından dönen dosya işleme sistemi  
Base64 veya URL olarak dönen dosyalar desteklenir ve otomatik olarak indirilebilir dosyalara dönüştürülür.

46. Modüler prompt sistemi  
Sistem prompt’ları bloklara ayrılmıştır ve dinamik olarak açılıp kapatılarak AI davranışı kontrol edilebilir.

47. Local provider başlatma otomasyonu  
Yerel AI sağlayıcıları, tanımlı komutlar ile otomatik olarak başlatılabilir veya durdurulabilir.

48. Sohbet farkındalıklı context oluşturma  
Sistem; son mesajlar, özetler, kod context’i ve ilgili hafıza parçalarını kullanarak akıllı bir context oluşturur.

49. Gerçek zamanlı bağımsız çoklu sohbet akışı  
Birden fazla sohbet aynı anda AI isteği gönderebilir. Her sohbet, diğer sohbetleri engellemeden gerçek zamanlı olarak bağımsız şekilde işlenir, yayınlanır ve güncellenir.

50. Entegre web arama  
Tavily kullanarak AI destekli web araması ve güncel çevrimiçi bilgi erişimi sağlar.  
Kullanıcı -> AI -> eğer gerekliyse(Kullanıcı İzni) -> Tavily -> AI -> Kullanıcı

51. CLI dinamik evet/hayır girişi  
CLI istemleri y, yes, n, no, yerelleştirilmiş o_Yes / o_No değerlerini ve bunların ilk harflerini kabul eder.
</details>

## 🔒 License  
<h1 align="center">📜 GPL-3.0 License</h1>

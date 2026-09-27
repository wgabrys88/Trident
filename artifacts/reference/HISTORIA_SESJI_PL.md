# Historia sesji

Oś czasu dla Wojciecha. To nie jest prawo. Prawo jest w `GOAL.md`, `AGENTS.md`, `RULES.md` i `BOTS.md`. Profil każdego fotela jest w `artifacts/reference/seats/`. Ten plik zbiera, co z tych sesji zostało w drzewie i dokąd idziemy dalej.

## Co już stoi

Na Iris działa lokalny stos głosu. `hear.py` nagrywa mikrofon laptopa i drukuje surowy transkrypt. `qwen.py` pyta Qwen3-0.6B, sam tekst. `gemma.py` pyta Gemmę 4, a obraz idzie jako surowe base64. `mouth.py` mówi jednym procesem, kawałek po kawałku, i odtwarza falę na głośnikach, gdy syntezuje następny kawałek. `assistant.py` spina słuchanie, mózg i usta, a potem się kończy. Nie uruchamia pięciu rezydentów i nie trzyma ich w pamięci. Na bliski czas ta pętla zostaje na Iris. Nie robi wywołania sieciowego. Grok jest poza tą ścieżką. Domyślny mózg to `qwen`.

Ask jest w wersji V2. Na Iris pytanie idzie wprost przez `qwen.py` w powłoce workera. Na Nvidii, tylko gdy polecenie ją nazywa, jest jeden agent chmurowy: model `composer-2.5`, `fast` false, sama komenda `gemma.py`. Stdout to odpowiedź. `gemma.py` i `qwen.py` odrzucają stderr dziecka, więc log ładowania modelu nie siada obok odpowiedzi. FINAL niesie skrót, najwyżej dwadzieścia linii bez tensorów. Po każdym Ask na Nvidii, zanim pójdzie FINAL, drzewo na tamtym klonie wraca lokalnie do `origin/runner-h`, gdy HEAD się rozjechał albo porcelain nie jest pusty. To domknięcie pytania, nie drugi cook.

Wave 3 zostaje w repozytorium jako notatka badawcza. `wave3_split.py` tnie tekst na oddech i na zmianę języka, `wave3_harness.py` odpala macierz na `sense.exe`, a plan jest w `artifacts/reference/WAVE3_PLAN.md`. Obok leży wzorzec mieszanego polskiego i angielskiego. To jest keep-research. `assistant.py` tej ścieżki nie woła. To nie jest produkt.

Obok asystenta stoi tor foteli. Pokój wojenny mieści sześć miejsc. Pracę Trident robią SPOC, Executor, Mouth, Ear i Ask. Local_IT_Guy jest fotelem projektu: komentuje zgodność LAN z dokumentem routera i czeka na GO we własnym czacie. Trasy są cztery. SPEAK idzie do Mouth. HEAR idzie do Ear, po sygnale głośników i po CONFIRM od SPOC. COOK, FIX i DOCS idą do Executora, od SPOC. ASK idzie do Ask, a FINAL wraca tylko do SPOC.

Rysunki w `artifacts/reference/design/` pokazują kształt na dziś i kształt późniejszy. `artifacts/reference/tracks/DEVICE_ROUTER.md` opisuje router zadań w LAN i mówi wprost, że go nie ma. Pule, na później, to `iris_cpu`, `iris_vulkan` i `nvidia_cuda`. Adresy są zapisane: Iris `192.168.16.45`, Nvidia `192.168.16.31`, brama `192.168.16.4`, ta sama podsieć, rezerwacja DHCP. Rysunek nie buduje programu.

Dokumenty da się odtworzyć z tagów, bez pamięci czatu.

- `MILESTONE-DOCS-RECREATE` (`8907c5faad16ff4edb0ff8f5c1ffe657ba7b7cf7`, krótko `8907c5f`) przepisuje żywe dokumenty od zera. Siedem plików foteli, tor asystenta na Iris i podpis rysunków.
- `MILESTONE-DOCS-RECREATE-SEATS` (`e7c8ac9da1c568b21c6d244728ef46fd5b635f9b`, krótko `e7c8ac9`) dopisuje zapisane identyfikatory foteli i nazwy przyszłej powierzchni zadań. Router nadal nie jest zbudowany.
- `MILESTONE-FRESH-BOOTSTRAP` (`3bcf9091e7265ba94b613b02eb1392a2fe982edb`, krótko `3bcf909`) dodaje `GROK_BOT_MASTER_PROMPT.md`. To tekst do wklejenia w czystego bota Grok po wipe i świeżym klonie. Ten tag zostaje na tym commicie.
- `MILESTONE-FRESH-BOOTSTRAP-PL` oznacza commit, który dodał ten plik i krótki odsyłacz w prompcie angielskim. Pierwszego tagu nie przesuwamy.

## Do czego idziemy

Najpierw asystent na Iris. Słuchanie, mózg i usta zostają na tej maszynie.

Później, dopiero po osobnym GO, router urządzeń i zrzut ciężkiego TTS. Wynik, który wraca, jest falą. Iris gra ją na głośnikach. Mikrofon i głośniki zostają na Iris.

Lokalne boty, jeśli powstaną, są osobnym pakietem. Mogą dzielić transport z torem asystenta. Nie są `assistant.py` i nie są SPOC-em.

Chmura zostaje. SPOC w chmurze nadal prowadzi fotel. Router, gdy kiedyś powstanie, go nie zastępuje. Peer shuttle w fazie 0–1 (tekst i małe pliki, identyczne peery, tylko LAN) czeka na GO.

## Jak to odtworzyć

Klon https://github.com/wgabrys88/Trident , gałąź `runner-h`.

| Tag | Commit | Co niesie |
| --- | --- | --- |
| `MILESTONE-DOCS-RECREATE` | `8907c5faad16ff4edb0ff8f5c1ffe657ba7b7cf7` | Żywe prawo i pliki foteli |
| `MILESTONE-DOCS-RECREATE-SEATS` | `e7c8ac9da1c568b21c6d244728ef46fd5b635f9b` | Zapisane id i niezbudowana powierzchnia zadań |
| `MILESTONE-FRESH-BOOTSTRAP` | `3bcf9091e7265ba94b613b02eb1392a2fe982edb` | `GROK_BOT_MASTER_PROMPT.md` |
| `MILESTONE-FRESH-BOOTSTRAP-PL` | commit tego pliku | Ta historia i odsyłacz w prompcie |

Prompt do wklejenia: `GROK_BOT_MASTER_PROMPT.md`. Ta historia: `artifacts/reference/HISTORIA_SESJI_PL.md`.

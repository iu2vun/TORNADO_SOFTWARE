"""Genera docs/Manuale_TornadoLogic.pdf (reportlab). Prima: python tools/make_screenshots.py"""
import os, sys
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.enums import TA_LEFT
from reportlab.platypus import (BaseDocTemplate, PageTemplate, Frame, Paragraph, Spacer, Image, Table, TableStyle,
                                PageBreak, KeepTogether, ListFlowable, ListItem, NextPageTemplate)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMG = os.path.join(ROOT, "docs", "img")
OUT = os.path.join(ROOT, "docs", "Manuale_TornadoLogic.pdf")
sys.path.insert(0, ROOT)
try:
    from main import APP_VERSION
except Exception:
    APP_VERSION = "0.1"

ORANGE = colors.HexColor("#ff8a1f"); DARK = colors.HexColor("#121417"); GREY = colors.HexColor("#5a6068")
LIGHT = colors.HexColor("#f3f4f6"); LINE = colors.HexColor("#d5d8dd")

FONT, FONTB, MONO = "Helvetica", "Helvetica-Bold", "Courier"
for reg, bold in (("DejaVuSans.ttf", "DejaVuSans-Bold.ttf"),):
    for d in ("/usr/share/fonts/truetype/dejavu", "C:/Windows/Fonts"):
        if os.path.exists(os.path.join(d, reg)):
            pdfmetrics.registerFont(TTFont("DV", os.path.join(d, reg)))
            pdfmetrics.registerFont(TTFont("DVB", os.path.join(d, bold)))
            FONT, FONTB = "DV", "DVB"
mono_path = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
if os.path.exists(mono_path):
    pdfmetrics.registerFont(TTFont("DVM", mono_path)); MONO = "DVM"

S = {
    "body": ParagraphStyle("body", fontName=FONT, fontSize=9.6, leading=13.6, textColor=colors.HexColor("#1c1f24"), spaceAfter=5),
    "h1": ParagraphStyle("h1", fontName=FONTB, fontSize=18, leading=22, textColor=DARK, spaceBefore=4, spaceAfter=8),
    "h2": ParagraphStyle("h2", fontName=FONTB, fontSize=12.2, leading=16, textColor=ORANGE, spaceBefore=10, spaceAfter=4),
    "cap": ParagraphStyle("cap", fontName=FONT, fontSize=8, leading=10.5, textColor=GREY, spaceAfter=8),
    "code": ParagraphStyle("code", fontName=MONO, fontSize=8.4, leading=11.2, backColor=LIGHT, borderPadding=(4, 5, 4, 5),
                           textColor=DARK, spaceBefore=3, spaceAfter=8, leftIndent=4, rightIndent=4),
    "note": ParagraphStyle("note", fontName=FONT, fontSize=9, leading=12.6, backColor=colors.HexColor("#fff4e6"),
                           borderColor=ORANGE, borderWidth=0.6, borderPadding=(5, 6, 5, 6), spaceBefore=4, spaceAfter=9,
                           textColor=colors.HexColor("#5a3300")),
    "cell": ParagraphStyle("cell", fontName=FONT, fontSize=8.7, leading=11.4),
    "cellb": ParagraphStyle("cellb", fontName=FONTB, fontSize=8.7, leading=11.4, textColor=colors.white),
    "toc": ParagraphStyle("toc", fontName=FONT, fontSize=10.4, leading=17, textColor=DARK),
}
P = lambda t, s="body": Paragraph(t, S[s])
def H1(t): return P(t, "h1")
def H2(t): return P(t, "h2")
def NOTE(t): return P("<b>Nota.</b> " + t, "note")
def CODE(t): return Paragraph(t.replace(" ", "&nbsp;").replace("\n", "<br/>"), S["code"])
def BUL(items): return ListFlowable([ListItem(P(i), leftIndent=12, bulletColor=ORANGE) for i in items], bulletType="bullet",
                                    start="•", leftIndent=12, bulletFontSize=9)
def TAB(rows, widths, head=True):
    data = [[Paragraph(c, S["cellb"] if (head and r == 0) else S["cell"]) for c in row] for r, row in enumerate(rows)]
    t = Table(data, colWidths=[w * mm for w in widths], repeatRows=1 if head else 0)
    st = [("GRID", (0, 0), (-1, -1), 0.4, LINE), ("VALIGN", (0, 0), (-1, -1), "TOP"),
          ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
          ("ROWBACKGROUNDS", (0, 1 if head else 0), (-1, -1), [colors.white, LIGHT])]
    if head: st += [("BACKGROUND", (0, 0), (-1, 0), DARK)]
    t.setStyle(TableStyle(st)); return t
def FIG(name, caption, w=172):
    from PIL import Image as PI
    im = PI.open(os.path.join(IMG, name + ".png")); r = im.height / im.width
    return KeepTogether([Image(os.path.join(IMG, name + ".png"), width=w * mm, height=w * mm * r),
                         P(caption, "cap")])

def page_deco(c, d):
    c.saveState()
    c.setFillColor(DARK); c.rect(0, A4[1] - 11 * mm, A4[0], 11 * mm, stroke=0, fill=1)
    c.setFillColor(ORANGE); c.rect(0, A4[1] - 11 * mm, 24 * mm, 11 * mm, stroke=0, fill=1)
    c.setFont(FONTB, 8.5); c.setFillColor(DARK); c.drawString(6 * mm, A4[1] - 7.2 * mm, "TORNADO")
    c.setFillColor(colors.white); c.setFont(FONT, 8.5)
    c.drawString(28 * mm, A4[1] - 7.2 * mm, f"Tornado Logic - Manuale d'uso - v{APP_VERSION}")
    c.setFillColor(GREY); c.setFont(FONT, 8)
    c.drawString(18 * mm, 10 * mm, "Tornado Laboratori")
    c.drawRightString(A4[0] - 18 * mm, 10 * mm, f"pag. {d.page}")
    c.restoreState()

def cover(c, d):
    c.saveState()
    c.setFillColor(DARK); c.rect(0, 0, A4[0], A4[1], stroke=0, fill=1)
    c.setFillColor(ORANGE); c.rect(0, A4[1] * 0.46, A4[0], 3, stroke=0, fill=1)
    # fulmine stilizzato
    p = c.beginPath(); x0, y0 = 38 * mm, A4[1] * 0.62
    pts = [(0, 40), (22, 40), (12, 0), (46, 52), (24, 52), (34, 92)]
    p.moveTo(x0 + pts[0][0] * mm * .8, y0 + pts[0][1] * mm * .8)
    for x, y in pts[1:]: p.lineTo(x0 + x * mm * .8, y0 + y * mm * .8)
    p.close(); c.setFillColor(ORANGE); c.drawPath(p, stroke=0, fill=1)
    c.setFillColor(colors.white); c.setFont(FONTB, 34); c.drawString(38 * mm, A4[1] * 0.38, "Tornado Logic")
    c.setFont(FONT, 14); c.setFillColor(ORANGE); c.drawString(38 * mm, A4[1] * 0.38 - 11 * mm, "Manuale d'uso")
    c.setFillColor(colors.HexColor("#c8ccd2")); c.setFont(FONT, 10.5)
    c.drawString(38 * mm, A4[1] * 0.38 - 24 * mm, "Analizzatore logico 8 canali 24 MHz (fx2lafw / sigrok)")
    c.drawString(38 * mm, A4[1] * 0.38 - 31 * mm, f"Versione {APP_VERSION}  -  Tornado Laboratori")
    c.restoreState()

doc = BaseDocTemplate(OUT, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=20 * mm, bottomMargin=17 * mm,
                      title="Tornado Logic - Manuale d'uso", author="Tornado Laboratori")
fr = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="f")
doc.addPageTemplates([PageTemplate(id="cover", frames=[fr], onPage=cover), PageTemplate(id="body", frames=[fr], onPage=page_deco)])

E = [NextPageTemplate("body"), PageBreak()]

# ------------------------------------------------------------------ indice
E += [H1("Indice"), P(
    "1. Che cos'e' Tornado Logic<br/>2. Installazione e dipendenze<br/>3. Collegamento dell'analizzatore<br/>"
    "4. Acquisizione e trigger<br/>5. Acquisizione continua e registro su file<br/>6. Decoder UART, SPI, I2C<br/>"
    "7. Analizzatore seriale RS-232 / RS-485 e Modbus RTU<br/>8. Misure e glitch<br/>9. Pinout e verifica dei collegamenti<br/>"
    "10. Tester ESP32<br/>11. Risoluzione dei problemi<br/>12. Riferimenti rapidi", "toc"), PageBreak()]

# ------------------------------------------------------------------ 1
E += [H1("1. Che cos'e' Tornado Logic"),
      P("Tornado Logic e' il programma di controllo, misura e analisi per l'analizzatore logico USB a 8 canali e 24 MHz "
        "(cloni Saleae Logic basati su Cypress FX2, riconosciuti da sigrok come <b>fx2lafw</b>). Il programma non parla direttamente con "
        "il dispositivo: a ogni acquisizione lancia <b>sigrok-cli</b> e ne legge i campioni, poi mostra le forme d'onda, decodifica i "
        "protocolli e calcola le misure."),
      BUL(["<b>Acquisizione</b> fino a 24 MS/s su 8 canali, con trigger software o hardware (sigrok), pre-trigger e modo continuo con registro su file.",
           "<b>Decoder</b> UART, SPI e I2C sovrapposti alle forme d'onda e tabellati.",
           "<b>Analizzatore seriale</b> RS-232 e RS-485 con stima automatica di baud e formato, tempi di DE/turnaround e decodifica <b>Modbus RTU</b> con controllo CRC.",
           "<b>Misure</b> per canale (frequenza, periodo, duty, jitter) e ricerca di <b>glitch</b>.",
           "<b>Pinout</b>: verifica automatica del collegamento filo-canale con il tester ESP32.",
           "<b>Tester ESP32</b>: genera segnali noti, anche con difetti voluti, per provare analizzatore e software."]),
      NOTE("Gli screenshot di questo manuale sono stati ottenuti con segnali simulati; nell'uso normale il programma lavora solo con l'hardware reale."),
      FIG("01_acquisizione", "Figura 1 - Finestra principale: pannello di acquisizione a sinistra, forme d'onda, tabella decoder in basso.")]

# ------------------------------------------------------------------ 2
E += [H1("2. Installazione e dipendenze"),
      P("Il programma funziona su Windows 10/11 a 64 bit. Oltre al programma servono tre componenti di sistema:"),
      TAB([["Componente", "A che serve", "Come si installa"],
           ["<b>sigrok-cli</b>", "Comunica con l'analizzatore e acquisisce i campioni.", "Automatico (vedi sotto)."],
           ["<b>Visual C++ 2010 Redistributable (x64)</b>", "Richiesto da alcune versioni di sigrok-cli.", "Automatico."],
           ["<b>Driver WinUSB</b> sull'analizzatore", "Senza di esso Windows non permette a sigrok di aprire il dispositivo.", "Automatico se l'analizzatore e' collegato, altrimenti con Zadig."],
           ["Inno Setup 6", "Solo per chi crea l'installer dai sorgenti.", "Automatico."]],
          [48, 70, 56]),
      Spacer(1, 4),
      H2("2.1 Installare tutto con un doppio clic"),
      P("Collega l'analizzatore alla USB ed esegui <b>installa_dipendenze.bat</b> (nella cartella del programma). Il file chiede da solo i "
        "permessi di amministratore, scarica e installa in modo silenzioso i componenti mancanti, salta quelli gia' presenti e "
        "alla fine controlla che sigrok-cli veda l'analizzatore. Il resoconto e' in <b>installa_dipendenze.log</b>."),
      CODE("installa_dipendenze.bat           tutto\ninstalla_dipendenze.bat noinno     senza Inno Setup"),
      P("Con l'installer del programma (<b>TornadoLogic-Setup-x.y.exe</b>) la stessa procedura parte come ultima voce dell'installazione "
        "(\"Installa sigrok-cli, Visual C++ e driver USB\"): tieni l'analizzatore collegato prima di concludere."),
      H2("2.2 Driver USB: se la procedura automatica non basta"),
      P("L'installazione del driver usa lo stesso motore di Zadig. Se non riesce, lo script apre <b>Zadig</b>: seleziona il dispositivo "
        "dell'analizzatore (se non compare: menu <i>Options &gt; List All Devices</i>), scegli <b>WinUSB</b> come driver e premi "
        "<i>Install Driver</i> o <i>Replace Driver</i>. Scollega e ricollega il cavo USB."),
      NOTE("Il driver si installa una volta per ogni porta USB/PC. Se cambi analizzatore o porta e non viene piu' riconosciuto, rilancia installa_dipendenze.bat con il dispositivo collegato."),
      H2("2.3 Creare l'eseguibile dai sorgenti"),
      P("Esegui <b>build.bat</b>: se mancano sigrok-cli o Inno Setup chiama prima installa_dipendenze.bat, poi crea l'ambiente Python, "
        "installa i pacchetti, compila con PyInstaller e crea l'installer in <i>dist\\installer</i>. Opzioni:"),
      CODE("build.bat               cartella dist\\TornadoLogic + installer\nbuild.bat onefile       file unico dist\\TornadoLogic.exe\nbuild.bat skipdeps      non controlla/installa le dipendenze di sistema"),
      P("Se sigrok-cli e' installato in un percorso insolito, imposta la variabile d'ambiente <b>TORNADO_SIGROK_CLI</b> con il percorso "
        "completo di sigrok-cli.exe.")]

# ------------------------------------------------------------------ 3
E += [H1("3. Collegamento dell'analizzatore"),
      BUL(["Collega i fili dei canali CH1...CH8 ai punti da misurare e <b>almeno un filo di massa (GND)</b> in comune con il circuito.",
           "Il livello massimo ammesso agli ingressi e' di circa 5 V: i segnali RS-232 veri (\u00b15...\u00b115 V) vanno prima convertiti a livello TTL (ad esempio con un MAX3232).",
           "Nella scheda <b>Pinout</b> si registra a quale pin del cavo/connettore corrisponde ciascun canale (vedi capitolo 9).",
           "All'avvio il programma lavora con <i>Hardware (sigrok)</i>: se sigrok-cli o il driver mancano, compare un messaggio chiaro nella barra di stato."]),
      H2("Primo controllo"),
      P("Premi <b>Acquisisci</b> (F5) con un segnale qualunque sul CH1. Se compare la forma d'onda, hardware, driver e sigrok-cli funzionano. "
        "Per un controllo completo dei fili usa il tester ESP32 con la funzione <i>Verifica collegamenti</i>."),
      H1("4. Acquisizione e trigger"),
      TAB([["Campo", "Significato"],
           ["Frequenza", "Frequenza di campionamento (fino a 24 MS/s). Scegline una almeno 4-5 volte la frequenza del segnale piu' veloce."],
           ["Campioni", "Numero di campioni della cattura; la durata e' mostrata subito sotto (campioni / frequenza)."],
           ["Trigger - Canale/Condizione", "Canale e fronte (salita, discesa, entrambi) o livello (alto, basso) che avvia la cattura."],
           ["Pre-trigger", "Percentuale della cattura registrata <i>prima</i> del trigger."],
           ["Trigger hardware (sigrok)", "Delega il trigger a sigrok-cli (vedi sotto)."]],
          [48, 126]),
      Spacer(1, 4),
      P("Con il <b>trigger software</b> il programma acquisisce una finestra di 2n campioni e cerca il fronte al suo interno: funziona sempre, "
        "ma la finestra reale e' piu' lunga della cattura mostrata. Con il <b>trigger hardware</b> sigrok attende il trigger (il chip FX2 non ha "
        "un trigger proprio: lo esegue libsigrok sul PC) e restituisce esattamente n campioni con il pre-trigger richiesto. Se sigrok "
        "rifiuta il trigger, il programma ripiega da solo sul software e lo segnala nella barra di stato e nel registro. Se il trigger non "
        "arriva entro il tempo limite l'acquisizione termina con un avviso di <i>trigger non arrivato</i>."),
      NOTE("Il trigger hardware e' attivo solo con dispositivo Hardware e con un canale di trigger scelto."),
      P("Sulla forma d'onda: i cursori <b>A</b> e <b>B</b> (clic e trascinamento) mostrano tempi, differenza e frequenza equivalente; "
        "<b>Adatta</b>, <b>Zoom +/-</b> e <b>Al trigger</b> sono nella scheda <i>Vista</i> del ribbon. I canali si rinominano e si nascondono dal riquadro <i>Canali</i>.")]

# ------------------------------------------------------------------ 5
E += [H1("5. Acquisizione continua e registro su file"),
      P("Spunta <b>Continua (ripeti fino a Stop)</b> per ripetere le acquisizioni una dopo l'altra, con una <i>Pausa</i> opzionale fra i cicli. "
        "Se un ciclo con trigger non lo vede, il ciclo viene segnalato ma la serie prosegue. <b>Stop</b> interrompe dopo il ciclo in corso."),
      P("Con <b>Registra su file</b> si sceglie una cartella e cosa salvare; ogni sessione crea una sottocartella <i>Log_AAAAMMGG_HHMMSS</i> con:"),
      BUL(["<b>eventi.log</b> - avvio, configurazione, ogni ciclo con esito e tempi, tentativi di riprova di sigrok, avvisi, fine;",
           "<b>decodifica.csv</b> - i dati decodificati di tutti i cicli;",
           "<b>catture\\*.npz</b> - le catture grezze, riapribili con <i>Apri</i>."]),
      FIG("06_continua_log", "Figura 2 - Acquisizione continua in corso: lo stato mostra il numero di ciclo."),
      H1("6. Decoder UART, SPI, I2C"),
      P("Nella scheda <b>Decoder</b> si attiva ogni protocollo, si indicano i canali e i parametri. Le annotazioni compaiono come corsie sotto i canali "
        "e nella tabella in basso; un doppio clic su una riga porta la vista sull'evento."),
      TAB([["Protocollo", "Parametri principali"],
           ["UART", "Canale, baud, bit dati, parita', bit di stop, LSB/MSB, linea invertita."],
           ["SPI", "Canali CLK/MOSI/MISO/CS, CPOL, CPHA, bit per parola, ordine dei bit, CS attivo basso."],
           ["I2C", "Canali SCL/SDA; indirizzi, dati e ACK/NACK."]],
          [30, 144]),
      FIG("02_decoder", "Figura 3 - Scheda Decoder: parametri a sinistra, annotazioni UART/SPI/I2C sulle forme d'onda."),
      NOTE("Se il baud e' troppo alto rispetto alla frequenza di campionamento (meno di 3 campioni per bit) la decodifica e' impossibile e il motivo e' scritto sotto la tabella. Errori ripetuti su una UART di solito indicano baud o formato impostati male (es. 115200 invece di 9600).")]

# ------------------------------------------------------------------ 7
E += [H1("7. Analizzatore seriale RS-232 / RS-485 e Modbus RTU"),
      P("Spunta <b>Abilita analizzatore seriale</b> nella scheda <b>Seriale</b>. Il programma stima da solo baud e formato (es. 19200 8E1), "
        "ricostruisce i messaggi e li mostra nella tabella <i>Seriale</i> con tempi e pause."),
      TAB([["Modo", "Canali da indicare", "Cosa si vede"],
           ["RS-232", "TX e RX (livello TTL)", "Messaggi per direzione, pausa e turnaround fra richiesta e risposta."],
           ["RS-485", "RO (uscita ricevitore), oppure A e B, piu' DE", "Direzione dedotta da DE (attivo = master che trasmette), anticipo e ritardo di DE, turnaround."]],
          [24, 64, 86]),
      Spacer(1, 4),
      P("Per l'RS-485 puoi usare <b>RO</b> (un solo canale, consigliato) oppure <b>A/B</b> (decodifica differenziale). Impulsi spuri piu' brevi di "
        "una frazione del bit vengono filtrati automaticamente e il filtro e' indicato nelle note."),
      H2("Modbus RTU"),
      P("Con <b>Decodifica Modbus RTU</b> ogni messaggio viene interpretato: indirizzo, funzione (FC03 lettura registri, ecc.), parametri, risposta, "
        "eccezioni e <b>controllo CRC16</b>. Le richieste senza risposta e le trasmissioni broadcast sono segnalate; il <i>tempo di risposta</i> "
        "(min/mediano/max) e' nel riepilogo. Una colonna <i>Modbus</i> compare nella tabella e una corsia <i>Modbus</i> sulle forme d'onda."),
      FIG("04_seriale_modbus", "Figura 4 - RS-485 Modbus RTU: richieste FC03 e risposte, DE anticipo/ritardo, turnaround e tempi di risposta."),
      NOTE("Se piu' della meta' dei frame risulta non valida il programma avvisa: di solito il traffico non e' Modbus (per esempio testo ASCII su RS-232) oppure baud/parita' sono errati.")]

# ------------------------------------------------------------------ 8
E += [H1("8. Misure e glitch"),
      P("La scheda <b>Misure</b> calcola per ogni canale numero di fronti di salita e di discesa, frequenza, periodo, duty e jitter picco-picco; "
        "con <b>Solo tra i cursori A e B</b> il calcolo si limita all'intervallo scelto."),
      FIG("03_misure", "Figura 5 - Tabella delle misure per canale."),
      P("La scheda <b>Glitch</b> elenca gli impulsi piu' brevi della soglia indicata (in ns): utile per trovare rimbalzi o disturbi. "
        "Un doppio clic su una riga porta la vista sul punto."),
      H1("9. Pinout e verifica dei collegamenti"),
      P("La scheda <b>Pinout</b> associa ai canali CH1...CH8 i pin del cavo o del connettore usato (default: pin 1...8). "
        "Il pulsante <b>Verifica collegamenti</b> acquisisce con il tester ESP32 in modo <i>walk</i> (un canale alto alla volta, ogni 500 ms) e "
        "controlla che ciascun filo sia sul canale atteso, riportando per ogni canale l'esito (<i>ok</i> oppure errore) e il pin associato. "
        "Se il tester e' collegato al programma, il modo walk viene impostato da solo e poi ripristinato."),
      FIG("05_pinout_verifica", "Figura 6 - Verifica collegamenti: tutti i canali risultano corretti.")]

# ------------------------------------------------------------------ 10
E += [H1("10. Tester ESP32"),
      P("Il tester e' una scheda ESP32 (firmware in <i>esp32_tester</i>, PlatformIO, board <i>esp32dev</i>) che genera su 8 uscite a 3,3 V i segnali "
        "noti usati dal programma. Si collega alla USB e nella scheda <b>Tester</b> si sceglie la porta COM e si preme <b>Connetti</b>."),
      FIG("07_tester", "Figura 7 - Scheda Tester: modi, difetti, LED delle uscite e console."),
      H2("Modi di funzionamento"),
      TAB([["Modo", "Uscite"],
           ["Fermo (off)", "Tutte le uscite ferme."],
           ["Misto (mixed)", "CH1 UART 115200 8N1 \"Hello\"; CH2-CH4 SPI; CH5-CH6 I2C; CH7 SDA; CH8 PWM 1 kHz 30 %."],
           ["Seriale (serial)", "CH1/CH2 RS-232 TX/RX 9600 8N1; CH3 RO, CH4 DE, CH5 A, CH6 B del Modbus RTU 19200 8E1; CH7 libero; CH8 PWM."],
           ["Walk", "Un canale alto alla volta, CH1 \u2192 CH8 ogni 500 ms (verifica fili)."]],
          [34, 140]),
      Spacer(1, 4),
      H2("Difetti voluti (solo modo seriale)"),
      P("Servono a provare i diagnostici del software. Valgono dal ciclo successivo e si azzerano con <b>Azzera difetti</b>:"),
      TAB([["Controllo", "Comando", "Effetto"],
           ["Baud", "<font name='%s'>baud &lt;%%&gt;</font>" % MONO, "Linea piu' veloce/lenta della percentuale indicata."],
           ["DE anticipo", "<font name='%s'>delead &lt;us&gt;</font>" % MONO, "Ritarda l'attivazione di DE rispetto allo start bit (es. -100 = DE attivo 100 us dopo)."],
           ["DE ritardo", "<font name='%s'>delag &lt;us&gt;</font>" % MONO, "Rilascia DE in anticipo rispetto alla fine del frame (es. -150 = 150 us prima)."],
           ["Errore di parita'", "<font name='%s'>parity on|off</font>" % MONO, "Bit di parita' invertito (errori su tutti i byte RS-485)."],
           ["CRC Modbus errato", "<font name='%s'>crc on|off</font>" % MONO, "Un bit del CRC invertito nelle risposte dei cicli dispari."]],
          [36, 38, 100]),
      Spacer(1, 4),
      P("La console in basso accetta direttamente i comandi del firmware (<font name='%s'>help, status, mode off|mixed|serial|walk, faults reset</font> e quelli sopra); "
        "le frecce su/giu' richiamano la cronologia. Dopo ogni riprogrammazione dell'ESP32 il tester parte in modo <i>mixed</i>: premi <b>Seriale</b> per il Modbus." % MONO),
      H2("Programmare l'ESP32"),
      CODE("cd esp32_tester\npio run -t upload      (scheda collegata alla USB)"),
      P("I pin proposti (GPIO 13, 14, 18, 19, 21, 22, 23, 25) sono in <i>include/config.h</i> e vanno verificati sulla propria scheda. "
        "<b>Mai collegare livelli RS-232 reali (\u00b112 V) direttamente agli ingressi.</b>")]

# ------------------------------------------------------------------ 11
E += [H1("11. Risoluzione dei problemi"),
      TAB([["Sintomo", "Causa probabile", "Rimedio"],
           ["\"sigrok-cli non trovato\"", "sigrok non installato o fuori dal PATH.", "Esegui installa_dipendenze.bat; oppure imposta TORNADO_SIGROK_CLI."],
           ["sigrok-cli parte ma non trova il dispositivo", "Driver non WinUSB, cavo/porta USB, dispositivo gia' aperto da PulseView.", "Chiudi PulseView, riesegui installa_dipendenze.bat con l'analizzatore collegato, oppure usa Zadig (WinUSB). Prova un'altra porta USB."],
           ["Acquisizione che fallisce a tratti (\"tentativi\")", "Il dispositivo si ri-enumera dopo il caricamento del firmware.", "Il programma riprova da solo con attese crescenti; i tentativi sono nel registro eventi."],
           ["\"Trigger non arrivato\"", "Il segnale non presenta la condizione scelta.", "Controlla canale e fronte, oppure prova senza trigger."],
           ["\"Baud non rilevabile\" su RS-485 con RO", "Pochi fronti o disturbi sulla linea.", "Aumenta i campioni, imposta il baud a mano; il filtro anti-impulsi e' gia' attivo."],
           ["Decodifica UART piena di errori", "Baud/formato errati o linea invertita.", "Verifica baud, bit, parita', e la spunta \"Linea invertita\"."],
           ["Modbus: molti frame non validi", "Traffico non Modbus o parametri sbagliati.", "Controlla baud e parita' (Modbus RTU spesso 19200 8E1 o 9600 8N1)."],
           ["Il tester non risponde", "Porta COM sbagliata o altra applicazione che la usa.", "Chiudi il monitor seriale di PlatformIO, scegli la porta giusta."],
           ["build.bat: PermissionError su dist", "Programma aperto o antivirus.", "Il bat chiude Tornado Logic e riprova; escludi la cartella del progetto dall'antivirus."]],
          [44, 52, 78]),
      Spacer(1, 6),
      H1("12. Riferimenti rapidi"),
      TAB([["Azione", "Comando"],
           ["Acquisisci", "F5"], ["Apri cattura (.npz)", "Ctrl+O"], ["Salva cattura", "Ctrl+S"],
           ["Esporta", "Menu <i>Esporta</i>: CSV (cambi di livello), VCD (GTKWave, PulseView)"],
           ["Dati dell'applicazione", "Log nella cartella scelta in <i>Registra su file</i>"],
           ["Log installazione dipendenze", "installa_dipendenze.log"]],
          [50, 124]),
      Spacer(1, 8),
      P("<b>Tornado Laboratori</b> - Tornado Logic v%s" % APP_VERSION, "cap")]

doc.build([Spacer(1, 1)] + E)
print("scritto", OUT)

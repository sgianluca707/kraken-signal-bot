# Versione locale e GitHub

Per l'installazione senza computer acceso, leggi `INSTALLAZIONE_GITHUB.md`.

# Kraken Signal Bot

Bot di monitoraggio che controlla ogni minuto:

- BTC/EUR
- ETH/EUR
- SOL/EUR
- XRP/EUR
- LINK/EUR
- AVAX/EUR
- ADA/EUR
- DOGE/EUR
- PAXG/EUR, con fallback XAUT/EUR o XAUT/USD se disponibili su Kraken

Analizza trend 1h, struttura 15m, breakout 5m e volume. Invia in una sola notifica Telegram tutti i nuovi setup credibili.

## Sicurezza

Il bot **non compra e non vende**. Usa soltanto endpoint pubblici Kraken, quindi non richiede chiavi Kraken. Non inserire mai password, seed phrase o chiavi private.

## 1. Installa Python

Serve Python 3.10 o successivo.

Verifica:

```bash
python3 --version
```

## 2. Crea il bot Telegram

1. Apri Telegram e cerca `@BotFather`.
2. Scrivi `/newbot`.
3. Segui le istruzioni e copia il token.
4. Apri una chat con il nuovo bot e premi Avvia.
5. Invia al bot un messaggio qualsiasi.
6. Nel browser apri:

```text
https://api.telegram.org/botIL_TUO_TOKEN/getUpdates
```

7. Cerca `"chat":{"id":...}` e copia quel numero.

Non condividere pubblicamente token e chat ID.

## 3. Imposta le variabili

### macOS / Linux

```bash
export TELEGRAM_BOT_TOKEN="incolla_token"
export TELEGRAM_CHAT_ID="incolla_chat_id"
```

### Windows PowerShell

```powershell
$env:TELEGRAM_BOT_TOKEN="incolla_token"
$env:TELEGRAM_CHAT_ID="incolla_chat_id"
```

## 4. Avvia

Dentro la cartella:

```bash
python3 bot.py
```

Il bot prova a risolvere automaticamente i nomi reali delle coppie tramite `AssetPairs`. Se una coppia non esiste, la salta e mostra un avviso.

## Prova senza Telegram

Avvia `python3 bot.py` senza impostare le variabili. Quando trova un setup, lo stampa nel terminale invece di inviarlo.

## Tenerlo acceso 24/7

Il computer deve rimanere acceso. Per un servizio continuo puoi eseguirlo su un piccolo server cloud, Raspberry Pi o computer domestico.

### Avvio semplice su macOS/Linux

```bash
nohup python3 bot.py > bot.log 2>&1 &
```

### Arresto

```bash
pkill -f "python3 bot.py"
```

## Personalizzazione in config.json

- `min_volume_ratio`: volume minimo rispetto alla media delle 20 candele precedenti.
- `max_breakout_extension_pct`: estensione massima dal livello rotto; predefinita 0,5%.
- `estimated_fee_each_side`: commissione stimata per lato. Il valore 0,004 equivale allo 0,4% ed è conservativo.
- `min_net_rr2`: rapporto rischio/rendimento netto minimo sul TP2.
- `scan_interval_seconds`: minimo 60 secondi.

## Come decide il segnale

Un breakout viene considerato solo quando:

1. EMA20 sopra EMA50 su 1h, EMA20 crescente e prezzo sopra EMA20.
2. La candela 5m completata chiude sopra la massima resistenza delle precedenti candele 15m.
3. Il volume 5m è almeno 1,5 volte la media recente.
4. Il prezzo corrente non è oltre lo 0,5% dal livello.
5. Il rapporto rischio/rendimento netto stimato resta sufficiente.

Il messaggio contiene sia:

- `COMPRA ORA — SPECULATIVO`, massimo 5% del capitale;
- `NON COMPRARE ANCORA — ATTENDI RETEST`, massimo 10% del capitale.

Il bot non ripete lo stesso segnale finché non compare un breakout tecnicamente nuovo.

## Limiti importanti

- Il polling ogni minuto non garantisce dati vecchi meno di due minuti in ogni circostanza: rete, API o computer possono rallentare.
- Il volume della candela chiusa è affidabile, ma il prezzo ticker può muoversi subito dopo.
- I livelli sono calcolati automaticamente e non sostituiscono il controllo umano.
- PAXG e XAUT sono token collegati all’oro: non sono oro fisico.
- Il calcolo netto usa una commissione configurabile, non la tua tariffa Kraken reale.
- Nessun segnale garantisce un guadagno.

**Questo segnale non garantisce un guadagno e con leva le perdite sono amplificate.**

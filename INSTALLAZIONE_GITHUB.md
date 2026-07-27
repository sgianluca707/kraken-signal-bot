# Installazione gratuita su GitHub Actions

Questa versione esegue una scansione circa ogni 5 minuti e non richiede che il computer rimanga acceso.

## Limite importante

GitHub Actions non è un feed real-time. I workflow programmati possono partire in ritardo, quindi non è possibile garantire dati sempre più recenti di 2 minuti. Per controlli realmente continui ogni minuto serve un server sempre acceso.

## Passaggi

### 1. Crea un account GitHub

Vai su GitHub e crea un account, se non ne hai già uno.

### 2. Crea un repository

1. Premi `New repository`.
2. Nome consigliato: `kraken-signal-bot`.
3. Per usare più facilmente GitHub Actions gratuitamente, puoi impostarlo pubblico. Non caricare mai token o password nei file.
4. Premi `Create repository`.

### 3. Carica i file

Nella pagina del repository:

1. Premi `uploading an existing file` oppure `Add file` → `Upload files`.
2. Carica **il contenuto** della cartella, compresa la cartella nascosta `.github`.
3. Controlla che esista questo percorso:

```text
.github/workflows/kraken-signals.yml
```

4. Premi `Commit changes`.

### 4. Crea il bot Telegram

1. Su Telegram apri `@BotFather`.
2. Scrivi `/newbot`.
3. Copia il token ricevuto.
4. Apri il tuo nuovo bot, premi `Avvia` e inviagli un messaggio.
5. Nel browser apri, sostituendo il token:

```text
https://api.telegram.org/botIL_TUO_TOKEN/getUpdates
```

6. Cerca `"chat":{"id":...}` e copia il numero dell’ID.

### 5. Inserisci i segreti in GitHub

Nel repository apri:

```text
Settings → Secrets and variables → Actions
```

Crea due `New repository secret`:

```text
Nome: TELEGRAM_BOT_TOKEN
Valore: il token dato da BotFather
```

```text
Nome: TELEGRAM_CHAT_ID
Valore: il numero chat ID
```

I valori non devono essere scritti in `config.json`, nel codice o nei messaggi del repository.

### 6. Abilita e prova il workflow

1. Apri la scheda `Actions`.
2. Se GitHub chiede conferma, abilita i workflow.
3. Seleziona `Kraken Signal Bot`.
4. Premi `Run workflow`.
5. Apri l’esecuzione e controlla che tutti i passaggi siano verdi.

Dopo la prova, GitHub lo eseguirà automaticamente circa ogni 5 minuti.

## Se non arriva alcun messaggio

Non significa necessariamente che il bot non funzioni: per impostazione invia notifiche solo quando trova un breakout valido e nuovo.

Per controllare:

1. Vai in `Actions`.
2. Apri l’ultima esecuzione.
3. Apri `Esegui scansione Kraken`.
4. Dovresti leggere `Nessun nuovo setup credibile` oppure il riepilogo inviato.

## Sicurezza

- Il bot usa soltanto dati pubblici Kraken.
- Non inserire chiavi Kraken.
- Non inserire password, seed phrase o codici 2FA.
- Non esegue ordini.
- PAXG/XAUT sono token collegati all’oro, non oro fisico.

**Questo segnale non garantisce un guadagno e con leva le perdite sono amplificate.**

# Aggiornamento dalla versione 2.0

## File da sostituire

Carica nel repository:

- `bot.py`
- `config.json`
- `state.json`
- `signal_history.csv`
- `requirements.txt`
- `README.md`
- l'intera cartella `.github`

## Importante

Il nuovo workflow deve poter aggiornare `state.json` e `signal_history.csv`.

Vai in:

`Settings → Actions → General → Workflow permissions`

Seleziona:

`Read and write permissions`

poi salva.

## Prima prova

1. Vai in `Actions`.
2. Apri `Kraken Signal Bot 3.0`.
3. Premi `Run workflow`.
4. Attiva `test_telegram` e avvia.
5. Controlla che Telegram riceva il messaggio di prova.
6. Avvia di nuovo senza `test_telegram`.
7. Apri il job `scan` e verifica la riga finale.

La riga `Nessun nuovo setup credibile.` indica che la scansione è riuscita ma non c'erano segnali validi.

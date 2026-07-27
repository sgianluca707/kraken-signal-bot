# Kraken Signal Bot 3.0 Definitivo

Bot **solo segnali** per Kraken Pro. Non compra, non vende e non usa chiavi API Kraken private.

## Funzioni principali

- 30 coppie EUR risolte dinamicamente tramite `AssetPairs`.
- Analisi 1h, 15m e 5m usando solo candele completate.
- Conferma del regime BTC per i segnali sulle altcoin.
- Filtri su volume, spread, volatilità ATR, qualità della candela ed estensione dal breakout.
- Punteggio qualità da 1 a 10; soglia predefinita 7,5.
- Stop, TP1, TP2 e R/R netto stimato dopo commissioni conservative.
- Cooldown di 6 ore per asset e deduplicazione.
- Massimo 4 segnali per scansione.
- Paper trading automatico e riepilogo giornaliero.
- Stato e storico CSV salvati dal workflow GitHub.
- Retry automatici e controlli sullo stato Kraken.

## Sicurezza

Il bot usa soltanto endpoint pubblici Kraken. I soli segreti richiesti sono quelli del bot Telegram:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

Non inserire mai password Kraken, seed phrase, chiavi private o codici 2FA.

## Installazione su GitHub

1. Carica tutti i file nella radice del repository, inclusa la cartella `.github`.
2. In `Settings → Secrets and variables → Actions` conserva i due segreti Telegram già creati.
3. In `Settings → Actions → General → Workflow permissions` seleziona **Read and write permissions**.
4. Apri `Actions → Kraken Signal Bot 3.0 → Run workflow`.
5. Per una prima prova seleziona `test_telegram`; poi esegui una scansione normale.

Il workflow programmato parte circa ogni 5 minuti. GitHub può occasionalmente ritardare le esecuzioni programmate; per dati veramente continui servirà in seguito un VPS.

## Comandi utili

```bash
python bot.py --validate
python bot.py --test-telegram
python bot.py --once --dry-run
python bot.py --once --dry-run --max-assets 3
```

## Personalizzazione prudente

Nel file `config.json`:

- `minimum_score`: 7,5 produce un numero moderato di segnali.
- `min_volume_ratio`: volume minimo rispetto alle 20 candele precedenti.
- `max_spread_pct`: evita coppie con spread troppo elevato.
- `risk_per_trade_pct`: rischio teorico usato solo nell'esempio Telegram.
- `max_position_pct`: limite della posizione teorica rispetto al capitale d'esempio.

## Limiti

- Il paper trading è una simulazione e non riproduce perfettamente slippage, latenza e riempimento degli ordini.
- Se stop e target vengono toccati nella stessa candela 5m, il bot registra prudentemente lo stop.
- Un buon punteggio non è una probabilità certa di successo.
- Nessun segnale garantisce un guadagno.

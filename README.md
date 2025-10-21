### Build Command

```sh
pyinstaller --add-data ".venv/Lib/site-packages/escpos/capabilities.json;escpos" --onefile --windowed app.py
```
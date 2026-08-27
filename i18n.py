"""i18n.py  -  Internationalization (i18n) helper for QECTOR Workbench.

Supports English (en), French (fr), and Japanese (ja).
"""

_current_lang = "en"

TRANSLATIONS = {
    "en": {
        "title": "QECTOR Decoder Workbench",
        "tab_code_explorer": "Code Explorer",
        "tab_decoder_lab": "Decoder Lab",
        "tab_benchmark": "Benchmark",
        "tab_batch_streaming": "Batch & Streaming",
        "tab_hardware": "Hardware",
        "tab_diagnostics": "Diagnostics",
        "tab_documentation": "Documentation",
        "tab_lab_info": "Lab & Personal Info",
        "tab_console": "Console",
        "theme": "Theme",
        "language": "Language",
        "save_profile": "Save Profile",
        "apply_license": "Apply Licence Key",
        "run_decode": "Run Decode",
        "run_batch": "Run Batch Decode",
        "queue_batch": "Queue Batch Decode",
        "clear_cache": "Clear Decoder Cache",
        "import_syndrome": "Import Syndrome",
        "compare_decoders": "Compare Decoders",
    },
    "fr": {
        "title": "Banc d'essai du décodeur QECTOR",
        "tab_code_explorer": "Explorateur de Code",
        "tab_decoder_lab": "Labo Décodeur",
        "tab_benchmark": "Banc d'essai",
        "tab_batch_streaming": "Lot & Streaming",
        "tab_hardware": "Matériel",
        "tab_diagnostics": "Diagnostics",
        "tab_documentation": "Documentation",
        "tab_lab_info": "Info Labo & Auteur",
        "tab_console": "Console",
        "theme": "Thème",
        "language": "Langue",
        "save_profile": "Enregistrer le profil",
        "apply_license": "Appliquer la clé de licence",
        "run_decode": "Lancer le décodage",
        "run_batch": "Lancer le lot",
        "queue_batch": "Mettre en file d'attente",
        "clear_cache": "Effacer le cache",
        "import_syndrome": "Importer le syndrome",
        "compare_decoders": "Comparer les décodeurs",
    },
    "ja": {
        "title": "QECTOR デコーダ ワークベンチ",
        "tab_code_explorer": "コードエクスプローラ",
        "tab_decoder_lab": "デコーダラボ",
        "tab_benchmark": "ベンチマーク",
        "tab_batch_streaming": "バッチとストリーミング",
        "tab_hardware": "ハードウェア",
        "tab_diagnostics": "自己診断",
        "tab_documentation": "ドキュメント",
        "tab_lab_info": "ラボ・個人情報",
        "tab_console": "コンソール",
        "theme": "テーマ",
        "language": "言語",
        "save_profile": "プロファイルを保存",
        "apply_license": "ライセンスキーを適用",
        "run_decode": "デコードを実行",
        "run_batch": "バッチデコードを実行",
        "queue_batch": "キューに追加",
        "clear_cache": "キャッシュをクリア",
        "import_syndrome": "シンドロームをインポート",
        "compare_decoders": "デコーダ比較",
    }
}

def set_language(lang: str) -> None:
    global _current_lang
    if lang.lower() in TRANSLATIONS:
        _current_lang = lang.lower()

def t(key: str) -> str:
    """Translate a key into the active language, falling back to English."""
    return TRANSLATIONS.get(_current_lang, TRANSLATIONS["en"]).get(key, TRANSLATIONS["en"].get(key, key))

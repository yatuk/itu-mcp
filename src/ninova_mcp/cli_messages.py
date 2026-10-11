"""Texts of the ``setup``, ``connect``, ``doctor`` and ``logout`` commands.

One table, Turkish first (the audience is İTÜ students), English second
(``--lang en``). No text here may be given a secret as a format argument.
"""

from __future__ import annotations

LANGUAGES = ("tr", "en")
DEFAULT_LANGUAGE = "tr"

# key -> (Turkish, English)
MESSAGES: dict[str, tuple[str, str]] = {
    # shared
    "level_ok": ("OK", "OK"),
    "level_warn": ("UYARI", "WARN"),
    "level_error": ("HATA", "ERROR"),
    "fix": ("çözüm: {fix}", "fix: {fix}"),
    "yes_no": ("[E/h]", "[Y/n]"),
    "client_claude-desktop": ("Claude Desktop", "Claude Desktop"),
    "client_claude-code": ("Claude Code", "Claude Code"),
    "client_cursor": ("Cursor", "Cursor"),
    "source_environment": ("ortam değişkenleri", "environment variables"),
    "source_env_file": (".env dosyası", ".env file"),
    "source_keychain": ("işletim sistemi anahtar zinciri", "operating system keychain"),
    "source_none": ("bulunamadı", "not found"),
    "reason_disabled": (
        "anahtar zinciri NINOVA_KEYRING=0 ile kapatılmış",
        "the keychain is switched off with NINOVA_KEYRING=0",
    ),
    "reason_not_installed": (
        '"keyring" paketi kurulu değil',
        'the "keyring" package is not installed',
    ),
    "reason_no_backend": (
        "bu sistemde kullanılabilir bir anahtar zinciri bulunamadı",
        "no usable keychain was found on this system",
    ),
    "reason_insecure_backend": (
        "bulunan keyring arka ucu gerçek bir işletim sistemi anahtar zinciri değil "
        "(düz metin veya boş arka uçlar kabul edilmez)",
        "the keyring backend found is not a real operating system keychain "
        "(plain text and null backends are refused)",
    ),
    "reason_write_failed": (
        "anahtar zinciri yazmayı reddetti",
        "the keychain refused the write",
    ),
    "fix_keyring": (
        'pipx install --force "itu-mcp[keyring]"  (veya: pipx inject itu-mcp keyring)',
        'pipx install --force "itu-mcp[keyring]"  (or: pipx inject itu-mcp keyring)',
    ),
    # setup
    "setup_intro": (
        "İTÜ MCP kurulumu. Kullanıcı adınız ve şifreniz bu bilgisayarın anahtar "
        "zincirine kaydedilir; hiçbir dosyaya veya ekrana yazılmaz.",
        "İTÜ MCP setup. Your username and password are stored in this computer's "
        "keychain; they are not written to any file or to the screen.",
    ),
    "setup_no_tty": (
        "Bu komut soru sorar, bu yüzden bir terminal penceresinde çalıştırılmalı. "
        "Terminali (Windows: PowerShell) açıp şunu yazın:\n  itu-mcp setup",
        "This command asks questions, so it needs a terminal window. "
        "Open a terminal (Windows: PowerShell) and run:\n  itu-mcp setup",
    ),
    "setup_username_prompt": (
        "İTÜ kullanıcı adı (e-posta, örn. soyad21@itu.edu.tr): ",
        "İTÜ username (the e-mail, e.g. surname21@itu.edu.tr): ",
    ),
    "setup_username_hint": (
        "Not: kullanıcı adı genellikle tam e-posta adresidir (ad@itu.edu.tr).",
        "Note: the username is usually the full e-mail address (name@itu.edu.tr).",
    ),
    "setup_password_prompt": (
        "İTÜ şifresi (yazarken görünmez): ",
        "İTÜ password (hidden while typing): ",
    ),
    "setup_empty": (
        "Kullanıcı adı ve şifre boş olamaz. Hiçbir şey kaydedilmedi.",
        "Username and password must not be empty. Nothing was saved.",
    ),
    "setup_env_overrides": (
        "Dikkat: NINOVA_USERNAME / NINOVA_PASSWORD şu anda ortamda veya bir .env "
        "dosyasında tanımlı. Sunucu önce onları kullanır, anahtar zincirini değil.",
        "Note: NINOVA_USERNAME / NINOVA_PASSWORD are currently set in the environment "
        "or a .env file. The server uses those first, not the keychain.",
    ),
    "setup_stored": (
        "Kaydedildi: işletim sistemi anahtar zinciri ({backend}).",
        "Saved to the operating system keychain ({backend}).",
    ),
    "setup_no_keychain": (
        "Şifre anahtar zincirine kaydedilemedi: {reason}.",
        "The password could not be stored in the keychain: {reason}.",
    ),
    "setup_fallback": (
        "Alternatif: bilgileri MCP istemcinizin ayar dosyasına kendiniz yazın. "
        "Aşağıdaki bloktaki iki değeri kendi bilgilerinizle değiştirin:",
        "Alternative: put the values into your MCP client's config file yourself. "
        "Replace the two placeholder values in this block:",
    ),
    "setup_verify_q": ("Giriş şimdi denensin mi?", "Try the login now?"),
    "setup_verifying": (
        "ninova.itu.edu.tr üzerinde giriş deneniyor...",
        "Trying the login at ninova.itu.edu.tr...",
    ),
    "setup_verify_ok": ("Giriş başarılı.", "Login works."),
    "setup_verify_failed": (
        "Giriş başarısız: {message}\nBilgileri kontrol edip tekrar deneyin: itu-mcp setup",
        "Login failed: {message}\nCheck the values and try again: itu-mcp setup",
    ),
    "setup_connect_q": (
        "Sunucu Claude Desktop / Claude Code / Cursor ayarlarına eklensin mi?",
        "Register the server in Claude Desktop / Claude Code / Cursor?",
    ),
    "setup_connect_later": (
        "İstemciye eklemek için daha sonra: itu-mcp connect",
        "To register it in a client later: itu-mcp connect",
    ),
    "setup_done": (
        "Kurulum tamam. Sorun olursa: itu-mcp doctor",
        "Setup finished. If something is off: itu-mcp doctor",
    ),
    # connect
    "connect_command": ("Kaydedilecek komut: {command}", "Command to register: {command}"),
    "connect_not_found": (
        "{client}: bu bilgisayarda bulunamadı, atlandı.",
        "{client}: not found on this computer, skipped.",
    ),
    "connect_none_found": (
        "Hiçbir istemci bulunamadı. Elle eklemek için istemcinizin MCP ayarına şu bloğu koyun:",
        "No client was found. To add it by hand, put this block into your client's MCP config:",
    ),
    "connect_created": ("{client}: eklendi (yeni dosya) -> {path}", "{client}: added (new file) -> {path}"),
    "connect_updated": ("{client}: eklendi -> {path}", "{client}: added -> {path}"),
    "connect_code_updated": (
        "{client}: `claude mcp add` ile eklendi.",
        "{client}: added with `claude mcp add`.",
    ),
    "connect_backup": ("  yedek: {path}", "  backup: {path}"),
    "connect_backup_password": (
        "  Dikkat: yedek dosyada eski şifre hâlâ duruyor. Her şey çalışınca silin.",
        "  Note: the backup still holds the old password. Delete it once everything works.",
    ),
    "connect_restart": (
        "  Değişikliğin görünmesi için {client} uygulamasını tamamen kapatıp açın.",
        "  Quit and reopen {client} for the change to show up.",
    ),
    "connect_unchanged": (
        "{client}: zaten kayıtlı, değişiklik yok.",
        "{client}: already registered, nothing changed.",
    ),
    "connect_dry_run": (
        "{client}: deneme modu, hiçbir şey yazılmadı. Yapılacak değişiklik:",
        "{client}: dry run, nothing was written. The change would be:",
    ),
    "connect_dry_run_command": (
        "{client}: deneme modu, çalıştırılacak komut:",
        "{client}: dry run, the command that would run:",
    ),
    "connect_invalid": (
        "{client}: {path} geçerli bir JSON nesnesi değil, dosyaya dokunulmadı. "
        "Şu bloğu elle ekleyin:",
        "{client}: {path} is not a valid JSON object, so it was left untouched. "
        "Add this block by hand:",
    ),
    "connect_has_password": (
        "{client}: mevcut `itu` kaydında şifre düz metin olarak duruyor ({path}).",
        "{client}: the existing `itu` entry holds the password in plain text ({path}).",
    ),
    "connect_password_no_keychain": (
        "  Anahtar zincirinde kayıtlı bilgi yok; önce `itu-mcp setup` çalıştırın. "
        "Kayıt değiştirilmedi.",
        "  Nothing is stored in the keychain yet; run `itu-mcp setup` first. "
        "The entry was left as it is.",
    ),
    "connect_replace_q": (
        "  Şifresiz kayıtla değiştirilsin mi (şifre anahtar zincirinden okunur)?",
        "  Replace it with an entry without the password (read from the keychain instead)?",
    ),
    "connect_password_kept": (
        "{client}: kayıt değiştirilmedi. Şifreyi dosyadan çıkarmak için: itu-mcp connect {target}",
        "{client}: the entry was left as it is. To take the password out of the file: "
        "itu-mcp connect {target}",
    ),
    "connect_manual": (
        "{client}: `claude` komutu PATH üzerinde bulunamadı. Şunu kendiniz çalıştırın:",
        "{client}: the `claude` command is not on PATH. Run this yourself:",
    ),
    "connect_exists_different": (
        "{client}: farklı bir `itu` kaydı zaten var. Değiştirmek için önce "
        "`claude mcp remove itu --scope user`, sonra:",
        "{client}: a different `itu` entry already exists. To replace it, first run "
        "`claude mcp remove itu --scope user`, then:",
    ),
    "connect_failed": (
        "{client}: `claude mcp add` başarısız oldu. Şunu kendiniz çalıştırın:",
        "{client}: `claude mcp add` failed. Run this yourself:",
    ),
    "connect_need_setup": (
        "Not: kayıtlı kullanıcı adı ve şifre bulunamadı. Önce: itu-mcp setup",
        "Note: no stored username and password were found. First run: itu-mcp setup",
    ),
    # logout
    "logout_session_deleted": ("Oturum dosyası silindi: {path}", "Session file deleted: {path}"),
    "logout_session_none": ("Kayıtlı oturum dosyası yok: {path}", "No session file at: {path}"),
    "logout_password_deleted": (
        "Kullanıcı adı ve şifre anahtar zincirinden silindi.",
        "Username and password were removed from the keychain.",
    ),
    "logout_password_none": (
        "Anahtar zincirinde kayıtlı bilgi yoktu.",
        "Nothing was stored in the keychain.",
    ),
    "logout_password_unavailable": (
        "Anahtar zincirine erişilemedi: {reason}.",
        "The keychain is not reachable: {reason}.",
    ),
    "logout_password_hint": (
        "Şifre anahtar zincirinde duruyor. Onu da silmek için: itu-mcp logout --forget-password",
        "The password is still in the keychain. To remove it too: "
        "itu-mcp logout --forget-password",
    ),
    # doctor
    "doc_python_ok": ("Python {version}", "Python {version}"),
    "doc_python_old": (
        "Python {version}: en az 3.11 gerekli",
        "Python {version}: 3.11 or newer is required",
    ),
    "doc_python_fix": (
        "python.org üzerinden Python 3.12 kurun, sonra: pipx reinstall itu-mcp",
        "install Python 3.12 from python.org, then: pipx reinstall itu-mcp",
    ),
    "doc_version": ("itu-mcp {version}", "itu-mcp {version}"),
    "doc_credentials": ("Giriş bilgileri: {source}", "Credentials: {source}"),
    "doc_credentials_fix": ("itu-mcp setup", "itu-mcp setup"),
    "doc_keychain_ok": ("Anahtar zinciri: {backend}", "Keychain: {backend}"),
    "doc_keychain_bad": ("Anahtar zinciri kullanılamıyor: {reason}", "Keychain unavailable: {reason}"),
    "doc_state_ok": ("Durum klasörü: {path}", "State directory: {path}"),
    "doc_state_missing": (
        "Durum klasörü henüz yok, ilk çalıştırmada oluşturulur: {path}",
        "State directory does not exist yet, it is created on first run: {path}",
    ),
    "doc_state_not_writable": (
        "Durum klasörüne yazılamıyor: {path}",
        "State directory is not writable: {path}",
    ),
    "doc_state_fix": (
        "klasörün sahibi olun veya NINOVA_STATE_DIR ile yazabildiğiniz bir klasör seçin",
        "own the directory, or choose a writable one with NINOVA_STATE_DIR",
    ),
    "doc_state_wide": (
        "Durum klasörü başka kullanıcılara açık (izin {mode}): {path}",
        "State directory is open to other users (mode {mode}): {path}",
    ),
    "doc_tz_ok": ("Saat dilimi verisi: Europe/Istanbul", "Time zone data: Europe/Istanbul"),
    "doc_tz_bad": (
        "Europe/Istanbul saat dilimi yüklenemedi",
        "The Europe/Istanbul time zone could not be loaded",
    ),
    "doc_tz_fix": ("pipx inject itu-mcp tzdata", "pipx inject itu-mcp tzdata"),
    "doc_env_ok": (
        "Çalışma klasöründeki .env: yok sayılan anahtar yok",
        ".env in the working directory: no ignored keys",
    ),
    "doc_env_ignored": (
        "Çalışma klasöründeki .env içinde yok sayılan anahtarlar: {keys}",
        "Keys ignored in the .env of the working directory: {keys}",
    ),
    "doc_env_ignored_fix": (
        "bunları istemci ayarındaki env bloğuna yazın veya dosyayı NINOVA_ENV_FILE ile gösterin",
        "set them in the client's env block, or name the file with NINOVA_ENV_FILE",
    ),
    "doc_env_file_ok": ("NINOVA_ENV_FILE: {path}", "NINOVA_ENV_FILE: {path}"),
    "doc_env_file_unset": ("NINOVA_ENV_FILE: tanımlı değil", "NINOVA_ENV_FILE: not set"),
    "doc_env_file_missing": (
        "NINOVA_ENV_FILE var olmayan bir dosyayı gösteriyor: {path}",
        "NINOVA_ENV_FILE points at a file that does not exist: {path}",
    ),
    "doc_env_file_fix": (
        "yolu düzeltin veya değişkeni kaldırın",
        "correct the path or remove the variable",
    ),
    "doc_plaintext": (
        "{client}: ayar dosyasında şifre düz metin olarak duruyor ({path})",
        "{client}: the config file holds the password in plain text ({path})",
    ),
    "doc_plaintext_fix": (
        "itu-mcp setup, ardından itu-mcp connect",
        "itu-mcp setup, then itu-mcp connect",
    ),
    "doc_config_invalid": (
        "{client}: ayar dosyası geçerli JSON değil ({path})",
        "{client}: the config file is not valid JSON ({path})",
    ),
    "doc_config_invalid_fix": (
        "dosyayı bir metin düzenleyicide düzeltin (yedeğiniz varsa geri alın)",
        "repair the file in a text editor (or restore a backup)",
    ),
    "doc_clients": ("Sunucunun kayıtlı olduğu istemciler: {clients}", "Clients with the server registered: {clients}"),
    "doc_clients_none": (
        "Sunucu hiçbir istemcide kayıtlı değil",
        "The server is not registered in any client",
    ),
    "doc_clients_fix": ("itu-mcp connect", "itu-mcp connect"),
    "doc_login_ok": ("Giriş denemesi: başarılı", "Login check: works"),
    "doc_login_bad": ("Giriş denemesi başarısız: {message}", "Login check failed: {message}"),
    "doc_login_fix": (
        "bilgileri yenileyin: itu-mcp setup",
        "store the values again: itu-mcp setup",
    ),
    "doc_pypi_ok": ("Sürüm güncel ({version})", "Up to date ({version})"),
    "doc_pypi_newer": (
        "Yeni sürüm var: {latest} (kurulu: {version})",
        "A newer version exists: {latest} (installed: {version})",
    ),
    "doc_pypi_fix": ("pipx upgrade itu-mcp", "pipx upgrade itu-mcp"),
    "doc_pypi_unknown": (
        "PyPI üzerindeki son sürüm okunamadı",
        "The latest version on PyPI could not be read",
    ),
    "doc_summary_ok": ("Sonuç: hata yok.", "Result: no errors."),
    "doc_summary_bad": ("Sonuç: {count} hata var.", "Result: {count} error(s)."),
}


def t(lang: str, key: str, **values: object) -> str:
    """Text for ``key`` in ``lang``; unknown languages fall back to Turkish."""
    turkish, english = MESSAGES[key]
    text = english if lang == "en" else turkish
    return text.format(**values) if values else text

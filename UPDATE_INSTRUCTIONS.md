# Як випустити оновлення Sleng Уніфікатор

Auto-update працює через `latest.json` що лежить у твоєму GitHub-репозиторії.
Учні відкривають додаток, він тицяє цей файл, і якщо там новіша версія —
показує діалог «Доступне оновлення» з кнопкою «📥 Оновити».

---

## Першочергова налаштування (один раз)

### 1. Створи GitHub-репозиторій

Йди на [github.com/new](https://github.com/new) і створи **public** репозиторій:

```
Name:  sleng-uniquifier
Visibility: ✓ Public
```

(Public — щоб raw.githubusercontent.com був доступний без авторизації.
Якщо приватний — потрібен Personal Access Token, складніше.)

### 2. Залий поточний код

```bash
cd "D:\VibeCode\модуль 2\Унікалізатор"
git init
git add .
git commit -m "Initial release v1.0.0 — фікс «сервер не доступний» + auto-update"
git remote add origin https://github.com/YOUR_USERNAME/sleng-uniquifier.git
git branch -M main
git push -u origin main
```

### 3. Створи перший Release

На GitHub:

1. Repo → вкладка **Releases** → **«Create a new release»**
2. **Tag**: `v1.0.0`
3. **Title**: `Sleng Уніфікатор 1.0.0`
4. **Description**: «Перший публічний реліз. Виправлено помилку «сервер не доступний», додано інтеграцію з Telegram-ботом, додано auto-update.»
5. **Attach binaries**: перетягни `dist\installer\SlengUniquifier_Setup_v1.0.0.exe`
6. **Publish release**

GitHub дасть тобі URL виду:
```
https://github.com/YOUR_USERNAME/sleng-uniquifier/releases/download/v1.0.0/SlengUniquifier_Setup_v1.0.0.exe
```

### 4. Створи `latest.json` у корені репо

Створи файл `latest.json` поруч з `package.json`:

```json
{
  "version": "1.0.0",
  "url": "https://github.com/YOUR_USERNAME/sleng-uniquifier/releases/download/v1.0.0/SlengUniquifier_Setup_v1.0.0.exe",
  "notes": "Перший публічний реліз. Виправлено помилку 'сервер не доступний', додано auto-update."
}
```

```bash
git add latest.json
git commit -m "Add latest.json for auto-update"
git push
```

### 5. Онови `UPDATE_INFO_URL` у `updater.js`

Відкрий `builds/electron/updater.js`, знайди:

```javascript
const UPDATE_INFO_URL =
  'https://raw.githubusercontent.com/YOUR_GITHUB_USER/sleng-uniquifier/main/latest.json';
```

Заміни `YOUR_GITHUB_USER` на свій username (наприклад `bodikbog`). Має вийти:

```javascript
const UPDATE_INFO_URL =
  'https://raw.githubusercontent.com/bodikbog/sleng-uniquifier/main/latest.json';
```

### 6. Перебілди з новим URL

```bash
cd "D:\VibeCode\модуль 2\Унікалізатор\builds\electron"
build.bat
```

Тепер ця збірка вже знає звідки тягнути updates.

---

## Випуск нового оновлення (повторювана процедура)

Коли ти виправив баг або додав фічу і хочеш дати студентам нову версію:

### 1. Підвищ версію у `package.json`

`builds/electron/package.json`:
```json
{
  "name": "sleng-uniquifier",
  "version": "1.0.1",
  ...
}
```

І у `setup_electron.iss`:
```
#define MyAppVersion   "1.0.1"
```

### 2. Білдь нову версію

```bash
cd "D:\VibeCode\модуль 2\Унікалізатор\builds\electron"
build.bat
```

→ отримаєш `dist\installer\SlengUniquifier_Setup_v1.0.1.exe`

### 3. Створи новий Release на GitHub

1. Releases → New release
2. Tag: `v1.0.1`
3. Опиши що змінилось
4. Прикріпи `SlengUniquifier_Setup_v1.0.1.exe`
5. Publish

### 4. Онови `latest.json` у репо

```json
{
  "version": "1.0.1",
  "url": "https://github.com/YOUR_USERNAME/sleng-uniquifier/releases/download/v1.0.1/SlengUniquifier_Setup_v1.0.1.exe",
  "notes": "Що змінилось:\n• Виправлено крах при відкритті mp4 з 4K\n• Прискорено обробку на ~20%"
}
```

```bash
git add latest.json
git commit -m "Release v1.0.1"
git push
```

### 5. Готово

В наступні 6 годин (або при наступному запуску) учні побачать діалог:
```
📦 Версія 1.0.1 вже доступна

Що змінилось:
• Виправлено крах при відкритті mp4 з 4K
• Прискорено обробку на ~20%

[📥 Оновити]  [⏳ Пізніше]
```

Натиснувши «📥 Оновити» — додаток сам завантажить, встановить, запустить нову версію.
Юзер нічого не робить.

---

## Гарячий тест ще ДО публікації для учнів

Якщо хочеш перевірити що update-flow реально працює:

1. Постав версію `1.0.0` у вже зібраному додатку (зараз воно так)
2. Створи `latest.json` з `version: "1.0.1"` (хай навіть посилання битий)
3. Поклади поточний інсталер `SlengUniquifier_Setup_v1.0.0.exe` як «v1.0.1» (типу зробив реліз)
4. Запусти додаток → побачиш діалог «Доступне оновлення»
5. Тицяй «📥 Оновити» → побачиш progress bar → installer → перезапуск
6. Версія залишиться 1.0.0 (бо файл насправді той самий), але цикл провалідуєш

---

## Що робити якщо update-flow зламався

1. У логах додатку (`%LOCALAPPDATA%\SlengUniquifier\server.log`) є рядки `[updater]`
2. Якщо там `check failed: HTTP 404` — твій latest.json не доступний
3. Якщо `HTTP 403` — GitHub забанив за rate-limit (60 req/hour без auth). Зробити репо public або зачекати годину.
4. Якщо download failed — переглянь URL у latest.json (має бути прямий до .exe з github releases)

---

## Безпека

- Учні качають .exe з GitHub Releases. GitHub підписує HTTPS і модерує malware → можна довіряти.
- Якщо хтось підмінить твій latest.json (наприклад зламає твій GitHub) — учні отримають його exe.
  Щоб цього уникнути:
  - Увімкни 2FA на GitHub
  - Не показуй Personal Access Token публічно
- Code signing (~$99-300/рік) — додатковий рівень захисту, не критично на старті.

---

## Хочеш ще краще?

- **Авто-changelog**: написати GitHub Action що генерує `latest.json` зі змісту release notes
- **Channel system**: окремий `beta.json` для тест-збірок, `latest.json` для всіх
- **Rollback**: якщо нова версія крашиться у >20% юзерів — автоматично шле їх назад на стару
- **Telemetry**: рахувати скільки юзерів вже на новій версії

Це все — окремі проєкти на потім.

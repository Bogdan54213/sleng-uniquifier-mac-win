from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# Реєструємо Arial з підтримкою кирилиці
pdfmetrics.registerFont(TTFont('Arial', 'C:/Windows/Fonts/arial.ttf'))
pdfmetrics.registerFont(TTFont('Arial-Bold', 'C:/Windows/Fonts/arialbd.ttf'))

output = r"D:\VibeCode\модуль 2\Унікалізатор\windows_install_guide.pdf"

doc = SimpleDocTemplate(
    output,
    pagesize=A4,
    leftMargin=20*mm,
    rightMargin=20*mm,
    topMargin=20*mm,
    bottomMargin=20*mm,
)

styles = getSampleStyleSheet()

title_style = ParagraphStyle(
    'Title',
    fontName='Arial-Bold',
    fontSize=22,
    textColor=colors.HexColor('#0d1b34'),
    alignment=TA_CENTER,
    spaceAfter=4,
)

subtitle_style = ParagraphStyle(
    'Subtitle',
    fontName='Arial',
    fontSize=11,
    textColor=colors.HexColor('#555555'),
    alignment=TA_CENTER,
    spaceAfter=16,
)

badge_style = ParagraphStyle(
    'Badge',
    fontName='Arial-Bold',
    fontSize=10,
    textColor=colors.white,
    backColor=colors.HexColor('#0d1b34'),
    alignment=TA_CENTER,
    spaceBefore=0,
    spaceAfter=20,
    borderPadding=(4, 12, 4, 12),
)

step_title_style = ParagraphStyle(
    'StepTitle',
    fontName='Arial-Bold',
    fontSize=14,
    textColor=colors.HexColor('#0d1b34'),
    spaceAfter=6,
    spaceBefore=4,
)

body_style = ParagraphStyle(
    'Body',
    fontName='Arial',
    fontSize=11,
    textColor=colors.HexColor('#222222'),
    spaceAfter=4,
    leading=16,
)

note_style = ParagraphStyle(
    'Note',
    fontName='Arial',
    fontSize=10,
    textColor=colors.HexColor('#92400e'),
    backColor=colors.HexColor('#fffbeb'),
    spaceAfter=8,
    spaceBefore=6,
    borderPadding=(8, 10, 8, 10),
    leading=15,
)

tip_style = ParagraphStyle(
    'Tip',
    fontName='Arial',
    fontSize=10,
    textColor=colors.HexColor('#166534'),
    backColor=colors.HexColor('#f0fdf4'),
    spaceAfter=4,
    spaceBefore=6,
    borderPadding=(8, 10, 8, 10),
    leading=15,
)

story = []

# Header
story.append(Spacer(1, 6))
story.append(Paragraph("Sleng Uniquifier", title_style))
story.append(Spacer(1, 8))
story.append(Paragraph("Інструкція встановлення для Windows", subtitle_style))
story.append(HRFlowable(width="100%", thickness=2, color=colors.HexColor('#0d1b34')))
story.append(Spacer(1, 18))

# Step 1
story.append(Paragraph("Крок 1 — Розпакуй архів", step_title_style))
story.append(Paragraph("1. Клікни <b>правою кнопкою миші</b> на завантажений .zip файл", body_style))
story.append(Paragraph("2. Вибери <b>Extract Here</b> (через WinRAR або 7-Zip)", body_style))
story.append(Paragraph("3. Всередині знайдеш файл <b>SlengUniquifier_Setup_v1.0.0.exe</b>", body_style))
story.append(Spacer(1, 4))
story.append(Paragraph("Якщо немає WinRAR або 7-Zip — завантаж безкоштовно: www.7-zip.org", note_style))
story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor('#e5e7eb')))
story.append(Spacer(1, 8))

# Step 2
story.append(Paragraph("Крок 2 — Встанови програму", step_title_style))
story.append(Paragraph("1. Двічі клікни на <b>SlengUniquifier_Setup_v1.0.0.exe</b>", body_style))
story.append(Paragraph("2. Може з'явитись попередження Windows Defender — це нормально", body_style))
story.append(Paragraph("3. Клікни <b>\"More info\"</b> → потім <b>\"Run anyway\"</b>", body_style))
story.append(Paragraph("4. Далі встановлюй як звичайну програму", body_style))
story.append(Spacer(1, 8))
story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor('#e5e7eb')))
story.append(Spacer(1, 8))

# Step 3
story.append(Paragraph("Крок 3 — Реєстрація", step_title_style))
story.append(Paragraph("1. Запусти програму після встановлення", body_style))
story.append(Paragraph("2. З'явиться форма реєстрації — введи своє <b>ім'я</b> та <b>контакт</b> (Telegram або email)", body_style))
story.append(Paragraph("3. Натисни <b>\"Відправити заявку\"</b>", body_style))
story.append(Spacer(1, 8))
story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor('#e5e7eb')))
story.append(Spacer(1, 8))

# Step 4
story.append(Paragraph("Крок 4 — Активація", step_title_style))
story.append(Paragraph("1. Отримай активаційний код від адміністратора", body_style))
story.append(Paragraph("2. Натисни <b>\"У мене є код\"</b>", body_style))
story.append(Paragraph("3. Введи отриманий код → натисни <b>\"Активувати\"</b>", body_style))
story.append(Paragraph("4. Готово — програма відкриється!", body_style))
story.append(Spacer(1, 10))

# Final tip
story.append(Paragraph("Готово! Якщо виникли проблеми — напиши адміністратору в Telegram.", tip_style))

story.append(Spacer(1, 20))
story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#e5e7eb')))
story.append(Spacer(1, 6))
story.append(Paragraph("Sleng Uniquifier v1.0.0  |  Windows інструкція", ParagraphStyle(
    'Footer',
    fontName='Arial',
    fontSize=9,
    textColor=colors.HexColor('#9ca3af'),
    alignment=TA_CENTER,
)))

doc.build(story)
print("PDF створено:", output)

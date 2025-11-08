# app/services/twilio_email.py
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import smtplib, os
from twilio.rest import Client
from ..settings import settings

def send_sms(body: str, to_phone: str) -> None:
    if not (settings.TWILIO_SID and settings.TWILIO_AUTH and settings.TWILIO_PHONE):
        print("[DEV] Twilio not configured. SMS would be:", body, "to", to_phone)
        return
    client = Client(settings.TWILIO_SID, settings.TWILIO_AUTH)
    client.messages.create(body=body, from_=settings.TWILIO_PHONE, to=to_phone)

def send_email(subject: str, to_email: str, body: str) -> None:
    if not (settings.GMAIL_USER and settings.GMAIL_PASS):
        print("[DEV] Gmail not configured. Email would be to", to_email, ":", subject)
        return
    msg = MIMEMultipart()
    msg["From"] = settings.GMAIL_USER
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    s = smtplib.SMTP("smtp.gmail.com", 587)
    s.starttls()
    s.login(settings.GMAIL_USER, settings.GMAIL_PASS)
    s.send_message(msg)
    s.quit()

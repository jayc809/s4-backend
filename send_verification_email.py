from decouple import config
from email.message import EmailMessage
import ssl
import smtplib


def send_verification_email(destination, code):
    app_username = config("GOOGLE_APP_USERNAME")
    app_password = config("GOOGLE_APP_PASSWORD")

    subject = "Verify your account"
    body = f"Your verification code is {code}"

    em = EmailMessage()
    em["From"] = app_username
    em["To"] = destination
    em["Subject"] = subject
    em.set_content(body)

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL('smtp.gmail.com', 465, context=context) as smtp:
            smtp.login(app_username, app_password)
            smtp.sendmail(app_username, destination, em.as_string())
    except:
        print("send email failed")
        raise Exception()

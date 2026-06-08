import umail

class EmailSender:
    def __init__(self, smtp_server , port, username, password):
        """
        Initialize the EmailSender class.

        :param smtp_server: SMTP server address (e.g., "smtp.gmail.com")
        :param port: SMTP server port (e.g., 587 for TLS, 465 for SSL)
        :param username: Email username (usually your email address)
        :param password: Email account password or app password
        """
        self.smtp_server = smtp_server
        self.port = port
        self.username = username
        self.password = password

    def send_email(self, subject, body, recipients):
        """
        Send an email to a list of recipients.

        :param subject: Email subject
        :param body: Email body content
        :param recipients: List of email addresses to send the email to
        """
        if not isinstance(recipients, list) or not recipients:
            raise ValueError("Recipients should be a non-empty list of email addresses.")

        # Create SMTP client
        smtp = umail.SMTP(self.smtp_server, self.port, ssl=(self.port == 465))

        try:
            # Login to the SMTP server
            smtp.login(self.username, self.password)

            # Prepare the email headers
            for recipient in recipients:
                smtp.to(recipient)
                smtp.write(f"Subject: {subject}\r\n")
                smtp.write(f"From: {self.username}\r\n")
                smtp.write(f"To: {recipient}\r\n\r\n")

                # Write the email body
                smtp.write(body)

                # Send the email
                smtp.send()
                print(f"Email sent to {recipient}")

        except Exception as e:
            print(f"Failed to send email: {e}")

        finally:
            smtp.quit()

# Example usage:
#email_sender = EmailSender("smtp.gmail.com", 465, "david.dupont1959@gmail.com", "myGooglePassword_2017")
#email_sender.send_email("Test Subject", "This is a test email.", ["david.dupont1959@gmail.com", "recipient2@example.com"])

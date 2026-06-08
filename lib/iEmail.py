import umail
import socket
import ssl
import ubinascii

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



    def _smtp_read(self, s, expect=None):
        """
        Read one SMTP response (handles multi-line).
        Returns list of raw lines (bytes). If 'expect' is set, checks final line starts with it.
        """
        lines = []
        line = s.readline()
        if not line:
            raise Exception("SMTP: no response")
        lines.append(line)
        # If multi-line (e.g., '250-...'), keep reading until final '250 ...'
        if len(line) >= 4 and line[3:4] == b'-':
            code = line[:3]
            while True:
                line = s.readline()
                if not line:
                    break
                lines.append(line)
                if line.startswith(code + b' '):
                    break
        for L in lines:
            print(L)  # debug
        if expect and not lines[-1].startswith(expect.encode()):
            raise Exception("SMTP error: " + lines[-1].decode(errors="ignore"))
        return lines

    def _smtp_send(self, s, cmd, expect=None):
        print(">>>", cmd)
        s.write((cmd + "\r\n").encode())
        return self._smtp_read(s, expect)

    def send_email_with_attachment(self, recipient, filename):
        # Read and base64-encode small file (keep attachments small on Pico)
        with open(filename, "rb") as f:
            file_data = f.read()
        encoded_file = ubinascii.b2a_base64(file_data).decode()

        boundary = "BOUNDARY12345"
        subject = f"Sending {filename} from Motion Sensor"
        body = f"Hello,\n\nHere is {filename} file from the Motion Sensor.\n"

        message = f"""From: {self.username}
To: {recipient}
Subject: Motion Detector file: {filename}
MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="{boundary}"

--{boundary}
Content-Type: text/plain; charset="utf-8"
Content-Transfer-Encoding: 7bit

{body}

--{boundary}
Content-Type: application/octet-stream; name="{filename}"
Content-Transfer-Encoding: base64
Content-Disposition: attachment; filename="{filename}"

{encoded_file}
--{boundary}--
"""

        # Connect to SMTP (implicit TLS on 465)
        addr = socket.getaddrinfo("smtp.gmail.com", 465)[0][-1]
        s = socket.socket()
        s.settimeout(20)
        s.connect(addr)
        s = ssl.wrap_socket(s)

        # 1) Read greeting FIRST (220 ...)
        self._smtp_read(s, expect="220")

        # 2) EHLO (multi-line 250)
        self._smtp_send(s, "EHLO pico", expect="250")

        # 3) AUTH LOGIN
        self._smtp_send(s, "AUTH LOGIN", expect="334")
        self._smtp_send(s, ubinascii.b2a_base64(self.username.encode()).decode().strip(), expect="334")
        self._smtp_send(s, ubinascii.b2a_base64(self.password.encode()).decode().strip(), expect="235")

        # 4) MAIL FROM / RCPT TO / DATA
        self._smtp_send(s, f"MAIL FROM:<{self.username}>", expect="250")
        self._smtp_send(s, f"RCPT TO:<{recipient}>", expect="250")
        self._smtp_send(s, "DATA", expect="354")

        # 5) Send message and end with CRLF . CRLF
        #DEBUG CODE
        print(message.encode())
        
        s.write(message.encode() + b"\r\n.\r\n")
        self._smtp_read(s, expect="250")

        # 6) QUIT
        self._smtp_send(s, "QUIT", expect="221")
        s.close()
        print("Email sent successfully!")



# Example usage:
#email_sender = EmailSender("smtp.gmail.com", 465, "david.dupont1959@gmail.com", "myGooglePassword_2017")
#email_sender.send_email("Test Subject", "This is a test email.", ["david.dupont1959@gmail.com", "recipient2@example.com"])

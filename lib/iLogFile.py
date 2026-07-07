import uos as os
import time

class LogManager:
    LEVELS = {
        "DEBUG": 10,
        "INFO" : 20,
        "ALERT": 30,
        "ERROR": 40,
    }

    def __init__(self, log_file="log.txt", max_size=1024, level="INFO"):
        """
        Initialize the LogManager.

        :param log_file: Path to the log file.
        :param max_size: Maximum size of the log file in bytes.
        :param level: Minimum log level to write to the file.
        """
        self.log_file = log_file
        self.max_size = max_size
        self.max_log_file_count = 10
        self.level = self.LEVELS.get(level, 20)
        self.file_index = self._get_log_file_number()	#The next number for rotated log files

    def _get_log_file_number(self):
        # Return the highest log file number incremented by one.
        # List the contents of the current directory
        directory_unsort = os.listdir()
        directory_list = sorted(directory_unsort)
        log_file_numbers = [0]

        # I only care about files that start with 'log_'
        for file in directory_list:
            underbar = file.find('_') + 1
            if file[0:underbar] == "log_":
                log_file_numbers.append(int(file[underbar:file.find('.')]))

            # Remove old files if there are more than max_log_file_count (10) rotated files.
            if len(log_file_numbers) >= self.max_log_file_count:
                for i in log_file_numbers[0:self.max_log_file_count - 1]:
                    del_file_name = f"log_{i}.txt"
                    try:
                        os.remove(del_file_name)
                    except OSError:
                        pass  # File may not exist, that's fine
                    
        return max(log_file_numbers) + 1
        
    def _get_timestamp(self):
        """
        Get the current timestamp. Returns a string in 'YYYY-MM-DD HH:MM:SS' format.
        """
        t = time.localtime()  # Requires RTC to be set
        return "{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(
            t[0], t[1], t[2], t[3], t[4], t[5]
        )

    def insert_number(self, filename, number):
        # Split the filename into name and extension
        name, ext = filename.rsplit('.', 1)
        new_filename = f"{name}_{number}.{ext}"
        return new_filename
    
    def _rotate_log(self):
        """Rotate log files if the main log file exceeds max_size."""
        try:
            file_size = os.stat(self.log_file)[6]  # Get file size in MicroPython
        except OSError:
            file_size = 0  # File does not exist yet

        if file_size >= self.max_size:
            # Remove the oldest log file if it exists
            try:
                os.remove("log_5.txt")
            except OSError:
                pass  # Ignore if file does not exist
            
            # Shift log files down (log_4.txt -> log_5.txt, log_3.txt -> log_4.txt, etc.)
            for i in range(4, 0, -1):
                old_log = f"log_{i}.txt"
                new_log = f"log_{i+1}.txt"
                try:
                    os.rename(old_log, new_log)
                except OSError:
                    pass  # Ignore if file does not exist
            
            # Rename the current log file to log_1.txt
            try:
                os.rename(self.log_file, "log_1.txt")
            except OSError:
                pass  # Ignore if file does not exist
            
            # Create a new empty log file
            with open(self.log_file, 'w') as f:
                pass


    def _write_log(self, level_name, message):
        """
        Write a log message to the file.
        """
        if self.LEVELS[level_name] >= self.level:
            log_entry = f"{self._get_timestamp()} - {level_name}: {message}\n"
            with open(self.log_file, "a") as f:
                f.write(log_entry)
            self._rotate_log()

    def debug(self, message):
        """Log a DEBUG message."""
        self._write_log("DEBUG", message)

    def info(self, message):
        """Log an INFO message."""
        self._write_log("INFO", message)

    def alert(self, message):
        """Log a ALERT message."""
        self._write_log("ALERT", message)

    def error(self, message):
        """Log an ERROR message."""
        self._write_log("ERROR", message)

'''
    2025/03/25 - Added variable and code to remember when time was last updated.

'''
from machine import RTC
import time

class RTCManager():
    
    def __init__(self, dateTime):
        self.rtc = RTC()
        self.setDateTime(dateTime)
        self.timeUpdated = dateTime

    def calculate_day_of_week(self, year, month, day):
        """
        Calculate the day of the week for a given date using Zeller's Congruence.
        
        Args:
            year (int): The year (e.g., 2024).
            month (int): The month (1-12).
            day (int): The day of the month (1-31).
            
        Returns:
            str: The name of the day of the week (e.g., "Sunday").
        """
        #On RP2: Day of Week = 0..6. Sunday = 0
        # Adjustments for Zeller's algorithm
        if month < 3:
            month += 12
            year -= 1

        # Zeller's Congruence formula
        K = year % 100
        J = year // 100

        # Calculate the day of the week
        f = day + (13 * (month + 1)) // 5 + K + (K // 4) + (J // 4) - (2 * J)
    #    day_of_week = f % 7
        day_of_week = (f + 6) % 7 # The (f + 6) adjusts so that 0 is Sunday

        # Day of the week mapping
    #    days = ["Saturday", "Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
        days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
        
    #    return days[day_of_week]
        return day_of_week #I really want the integer version

    def is_daylight_savings(self, year, month, day):
        # Check for months outside of DST range
        if month < 3 or month > 11:
            return False
        if month > 3 and month < 11:
            return True
        
        # For March, DST starts on the second Sunday
        if month == 3:
            # Calculate the date of the second Sunday in March
            first_day = time.localtime(time.mktime((year, 3, 1, 0, 0, 0, 0, 0, -1)))[7]
    #        print(first_day)
            dst_start = 14 - first_day
            return day >= dst_start
        
        # For November, DST ends on the first Sunday
        if month == 11:
            # Calculate the date of the first Sunday in November
            first_day = time.localtime(time.mktime((year, 11, 1, 0, 0, 0, 0, 0, -1)))[7]
    #        print(first_day)
            dst_end = 7 - first_day
            return day < dst_end
        
        # For all other months in the range
        return True

    def setDateTime(self, dateTime):  #dateTime is a list of[year, month, date, hour, minute, second]    
        global timeUpdated
        #This sets up the RTC
        '''The 8-tuple has the following format:
            (year, month, day, weekday, hours, minutes, seconds, subseconds)
        '''
        # Get the day of week
        dateTime.insert(3, self.calculate_day_of_week(dateTime[0], dateTime[1], dateTime[2]))

        #DST is only used to light an LED
        isDST = not(self.is_daylight_savings(dateTime[0], dateTime[1], dateTime[2])) # LEDs are wired so that 0 (False) turns them on

        self.rtc.datetime((dateTime[0], dateTime[1], dateTime[2], dateTime[3], dateTime[4], dateTime[5], dateTime[6], 0))
        
        self.time_Updated = dateTime

        return

    def get_time_part(self, part):
        index_map = {
            "year": 0,
            "month": 1,
            "date": 2,
            "day_of_week": 3,
            "hour": 4,
            "minute": 5,
            "second": 6,
            "sub_second": 7
            }
        return self.rtc.datetime()[index_map[part]]
        
    def get_formatted_time(self):
        year, month, day, weekday, hours, minutes, seconds, subseconds = self.rtc.datetime()
        return f"{year:04d}-{month:02d}-{day:02d} {hours:02d}:{minutes:02d}:{seconds:02d}"

    def seconds_to_time(self, total_seconds):
        # Convert to local time structure
        local_time = time.localtime(total_seconds)

        # Format as YYYY-MM-DD HH:MM:SS
        formatted_datetime = f"{local_time[0]:04}-{local_time[1]:02}-{local_time[2]:02} {local_time[3]:02}:{local_time[4]:02}:{local_time[5]:02}"
        return formatted_datetime

    def check_time(self, weekday, hour, minute):
        # check if current time matches input parameters
        # Get the current date and time
        current_time = self.rtc.datetime()

        # return boolean
        return(current_time[3] == weekday and current_time[4] == hour and current_time[5] == minute)
        
        
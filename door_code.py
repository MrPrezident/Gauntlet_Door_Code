#!/home/nathan/Desktop/Gauntlet/venv/bin/python3

import time
import random
import os
import adafruit_matrixkeypad
from digitalio import DigitalInOut, Direction, Pull
import board
import threading
import subprocess
from gpiozero import DigitalOutputDevice

# Create the sounds directory if it doesn't exist
sounds_dir = "sounds"
os.makedirs(sounds_dir, exist_ok=True)

class GpioLcd:
    def __init__(self, rs, e, d4, d5, d6, d7):
        self.rs = DigitalOutputDevice(rs)
        self.e = DigitalOutputDevice(e)
        self.d4 = DigitalOutputDevice(d4)
        self.d5 = DigitalOutputDevice(d5)
        self.d6 = DigitalOutputDevice(d6)
        self.d7 = DigitalOutputDevice(d7)
        
        self._init_lcd()
        self.current_message = ""

    def _pulse_enable(self):
        self.e.on()
        time.sleep(0.0005)
        self.e.off()
        time.sleep(0.0005)

    def _write_four_bits(self, data):
        self.d4.value = (data & 0x01) != 0
        self.d5.value = (data & 0x02) != 0
        self.d6.value = (data & 0x04) != 0
        self.d7.value = (data & 0x08) != 0
        self._pulse_enable()

    def _send(self, data, mode):
        self.rs.value = mode
        self._write_four_bits(data >> 4)
        self._write_four_bits(data)

    def _init_lcd(self):
        self._send(0x33, False)
        self._send(0x32, False)
        self._send(0x28, False)
        self._send(0x0C, False)
        self._send(0x06, False)
        self._send(0x01, False)
        time.sleep(0.005)

    def clear(self):
        self._send(0x01, False)
        time.sleep(0.005)

    def write(self, message):
        if message != self.current_message:
            self.clear()
            self.current_message = message
            for char in message:
                if char == '\n':
                    self._send(0xC0, False)  # Move to second line
                else:
                    self._send(ord(char), True)

def write_lcd(lcd, message):
    lock = threading.Lock()
    with lock:
        lcd.write(message)

def generate_code():
    return f"{random.randint(1000, 9999)}"

def generate_wav(code, filename, ready_event):
    new_wav_path = os.path.join(sounds_dir, filename)
    os.system(f"echo \"The code is {' '.join(code)}\" | ./venv/bin/piper --model en_US-l2arctic-medium.onnx --output_file {new_wav_path}")
    ready_event.set()  # Indicate that the new WAV file is ready

def clean_old_wavs(current_wav, new_wav):
    for wav_file in os.listdir(sounds_dir):
        wav_path = os.path.join(sounds_dir, wav_file)
        if wav_path not in (current_wav, new_wav):
            os.remove(wav_path)

def speak_code_piper(code_list, last_code, regenerate_event, code_accepted_event):
    current_wav = None
    old_wav = None
    ready_event = threading.Event()

    # Generate the initial WAV file
    regenerate_event.set()

    while True:
        if regenerate_event.is_set():
            old_wav = current_wav
            new_wav = f"code_{code_list[0]}_{time.time()}.wav"
            new_wav_path = os.path.join(sounds_dir, new_wav)
            ready_event.clear()
            threading.Thread(target=generate_wav, args=(code_list[0], new_wav, ready_event)).start()
            regenerate_event.clear()

        if ready_event.is_set():
            current_wav = new_wav_path
            clean_old_wavs(old_wav, current_wav)

        if (not code_accepted_event.is_set()) and current_wav and os.path.exists(current_wav):
            os.system(f"aplay {current_wav}")
        time.sleep(1)

def setup_keypad():
    cols = [DigitalInOut(x) for x in (board.D13, board.D5, board.D26)]
    rows = [DigitalInOut(x) for x in (board.D6, board.D21, board.D20, board.D19)]
    keys = ((1, 2, 3),
            (4, 5, 6),
            (7, 8, 9),
            ('*', 0, '#'))
    keypad = adafruit_matrixkeypad.Matrix_Keypad(rows, cols, keys)
    return keypad

def monitor_keypad(keypad, code_list, gpio_pin, lcd, activity_event, phone_off_hook, code_accepted_event, pickup_mode, new_code_event):
    entered_code = []
    debounce_time = 0.2  # 200 milliseconds debounce time
    last_key_time = time.monotonic()

    while True:
        if pickup_mode.is_set():
            time.sleep(0.1)
            continue

        keys = keypad.pressed_keys
        if keys and (time.monotonic() - last_key_time) > debounce_time:
            last_key_time = time.monotonic()
            activity_event.set()  # Reset inactivity timer
            for key in keys:
                if key == '*' or key == '#':
                    entered_code = []
                    write_lcd(lcd, "Enter Code:")
                else:
                    entered_code.append(str(key))
                    if len(entered_code) > 4:
                        entered_code.pop(0)  # Keep only the last 4 keys
                    print("Pressed: ", key)
                    print("Entered code: ", ''.join(entered_code))
                    write_lcd(lcd, f"Enter Code:\n{''.join(entered_code)}")
                    if ''.join(entered_code) in code_list:
                        print("Code accepted")
                        write_lcd(lcd, "Code Accepted")

                        entered_code = []  # Reset after a correct code is entered
                        code_list.clear()
                        clean_old_wavs("","")
                        new_code_event.set()
 
                        code_accepted_event.set()
                        gpio_pin.value = True  # Set GPIO pin high
                        time.sleep(10)  # Keep it high for 10 seconds
                        gpio_pin.value = False  # Set GPIO pin low
                        code_accepted_event.clear()
                        
                        activity_event.set()
                        if not phone_off_hook.is_set():
                            pickup_mode.set()
                            write_lcd(lcd, "Pick up the\nphone!")
                        else:
                            write_lcd(lcd, "Enter Code:\n")
        time.sleep(0.1)

def monitor_mute_button(mute_pin, lcd, activity_event, phone_off_hook, pickup_mode, code_accepted_event):
    while True:
        if not mute_pin.value:  # GPIO27 is grounded (phone on hook)
            subprocess.run(["amixer", "-q", "sset", "Master", "Playback", "Switch", "off"])
            activity_event.clear()  # Reset inactivity
            phone_off_hook.clear()  # Indicate the phone is on the hook
        else:
            subprocess.run(["amixer", "-q", "sset", "Master", "Playback", "Switch", "on"])
            phone_off_hook.set()  # Indicate the phone is off the hook
            if not code_accepted_event.is_set() and pickup_mode.is_set():
                write_lcd(lcd, "Enter Code:\n")
                pickup_mode.clear()
            activity_event.set()  # Start inactivity timer
        time.sleep(0.1)

def monitor_inactivity(lcd, activity_event, phone_off_hook, pickup_mode, code_list, new_code_event):
    while True:
        activity_event.wait()  # Wait until activity is detected
        activity_event.clear()  # Reset the event to start the inactivity timer

        # Wait for 30 seconds of inactivity
        if not activity_event.wait(30):
            if phone_off_hook.is_set():
                continue  # Do not change to "Pick up the phone!" if the phone is off the hook
            write_lcd(lcd, "Pick up the\nphone!")
            pickup_mode.set()
            code_list.clear()
            clean_old_wavs("","")
            new_code_event.set() 

def main():
    # Setup keypad
    keypad = setup_keypad()

    # Setup GPIO pin
    gpio_pin = DigitalInOut(board.D17)
    gpio_pin.direction = Direction.OUTPUT

    # Setup mute button GPIO
    mute_pin = DigitalInOut(board.D27)
    mute_pin.direction = Direction.INPUT
    mute_pin.pull = Pull.UP  # Use internal pull-up resistor

    # Setup LCD
    RS = 23
    E = 18
    D4 = 24
    D5 = 25
    D6 = 22
    D7 = 10
    lcd = GpioLcd(RS, E, D4, D5, D6, D7)

    # Initialize LCD display
    lcd.clear()
    write_lcd(lcd, "Pick up the\nphone!")

    # Code list to keep track of the last two codes
    new_code = generate_code()
    code_list = [new_code, new_code]
    last_code = [""]
    regenerate_event = threading.Event()
    regenerate_event.set()  # Trigger initial WAV generation
    print(f"Initial codes: {code_list}")
    # Event to monitor activity
    activity_event = threading.Event()
    phone_off_hook = threading.Event()
    pickup_mode = threading.Event() 
    code_accepted_event = threading.Event()
    new_code_event = threading.Event()

    pickup_mode.set()

    # Create threads for speaking code, monitoring keypad, and monitoring mute button
    speech_thread = threading.Thread(target=speak_code_piper, args=(code_list, last_code, regenerate_event, code_accepted_event))
    keypad_thread = threading.Thread(target=monitor_keypad, args=(keypad, code_list, gpio_pin, lcd, activity_event, phone_off_hook, code_accepted_event, pickup_mode, new_code_event))
    mute_thread = threading.Thread(target=monitor_mute_button, args=(mute_pin, lcd, activity_event, phone_off_hook, pickup_mode, code_accepted_event))
    inactivity_thread = threading.Thread(target=monitor_inactivity, args=(lcd, activity_event, phone_off_hook, pickup_mode, code_list, new_code_event))

    # Start threads
    speech_thread.start()
    keypad_thread.start()
    mute_thread.start()
    inactivity_thread.start()

    # Generate a new code every 60 seconds
    while True:
        #time.sleep(60)
        new_code_event.wait(60)
        new_code_event.clear()
        new_code = generate_code()
        while len(code_list) > 1:
            code_list.pop()  # Remove the oldest code
        while len(code_list) < 2:
            code_list.insert(0, new_code)  # Insert the new code at the beginning
        print(f"New code: {new_code} Code List: {code_list}")
        regenerate_event.set()  # Trigger WAV generation

if __name__ == "__main__":
    main()

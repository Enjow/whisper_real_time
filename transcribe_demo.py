#! python3.7

import argparse
import io
import os
import speech_recognition as sr
from faster_whisper import WhisperModel

from datetime import datetime, timedelta
from queue import Queue
from tempfile import NamedTemporaryFile
from time import sleep
from sys import platform


def main():
    DELAY_S = 5 ## input parameter in the future. set for 30s for now. 
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="medium", help="Model to use",
                        choices=["tiny", "base", "small", "medium", "large"])
    parser.add_argument("--non_english", action='store_true',
                        help="Don't use the english model.")
    parser.add_argument("--energy_threshold", default=1000,
                        help="Energy level for mic to detect.", type=int)
    parser.add_argument("--record_timeout", default=2,
                        help="How real time the recording is in seconds.", type=float)
    parser.add_argument("--phrase_timeout", default=1.5,
                        help="How much empty space between recordings before we "
                             "consider it a new line in the transcription.", type=float)
    parser.add_argument("--microphone_input", default=None,
                        help="Which microphone to use. Standard set to default input.", type=str)
    if 'linux' in platform:
        parser.add_argument("--default_microphone", default='pulse',
                            help="Default microphone name for SpeechRecognition. "
                                 "Run this with 'list' to view available Microphones.", type=str)
    args = parser.parse_args()

    # The last time a recording was retrieved from the queue.
    phrase_time = None
    # Current raw audio bytes.
    last_sample = bytes()
    # Thread safe Queue for passing data from the threaded recording callback.
    data_queue = Queue()
    # We use SpeechRecognizer to record our audio because it has a nice feature where it can detect when speech ends.
    recorder = sr.Recognizer()
    recorder.energy_threshold = args.energy_threshold
    # Definitely do this, dynamic energy compensation lowers the energy threshold dramatically to a point where the SpeechRecognizer never stops recording.
    recorder.dynamic_energy_threshold = False

    # Important for linux users.
    # Prevents permanent application hang and crash by using the wrong Microphone
    if 'linux' in platform:
        mic_name = args.default_microphone
        if not mic_name or mic_name == 'list':
            print("Available microphone devices are: ")
            for index, name in enumerate(sr.Microphone.list_microphone_names()):
                print(f"Microphone with name \"{name}\" found")
            return
        else:
            for index, name in enumerate(sr.Microphone.list_microphone_names()):
                if mic_name in name:
                    source = sr.Microphone(sample_rate=16000, device_index=index)
                    break
    else: 
        if args.microphone_input:
            # mic_name = "MacBook Pro Speakers"
            # mic_name = "MacBook Pro Microphone"
            for index, name in enumerate(sr.Microphone.list_microphone_names()):
                if args.microphone_input in name:
                    device_index = index
                    print(f'Using microphone with index {index}, called {name}')
        else:
            device_index = None

        source = sr.Microphone(device_index=device_index, sample_rate=16000) # Use output as microphone.
        # source = sr.Microphone(sample_rate=16000) # Use output as microphone.

    # Load / Download model
    model = args.model
    if args.model != "large" and not args.non_english:
        model = model + ".en"
    
    audio_model = WhisperModel(model) # added by me -- THIS LINE 

    record_timeout = args.record_timeout
    phrase_timeout = args.phrase_timeout

    temp_file = NamedTemporaryFile().name
    transcription = ['']
    transcription_times = [datetime.utcnow()]

    with source:
        recorder.adjust_for_ambient_noise(source)

    def record_callback(_, audio:sr.AudioData) -> None:
        """
        Threaded callback function to receive audio data when recordings finish.
        audio: An AudioData containing the recorded bytes.
        """
        # Grab the raw bytes and push it into the thread safe queue.
        data = audio.get_raw_data()
        data_queue.put(data)

    # Create a background thread that will pass us raw audio bytes.
    # We could do this manually but SpeechRecognizer provides a nice helper.
    recorder.listen_in_background(source, record_callback, phrase_time_limit=record_timeout)

    # Cue the user that we're ready to go.
    print("Model loaded.\n")

    phrase_counter = 0

    while True:
        try:
            now = datetime.utcnow()
            # Pull raw recorded audio from the queue.
            if not data_queue.empty():
                phrase_complete = False
                # If enough time has passed between recordings, consider the phrase complete.
                # Clear the current working audio buffer to start over with the new data.
                if phrase_time and now - phrase_time > timedelta(seconds=phrase_timeout):
                    last_sample = bytes()
                    phrase_complete = True
                    phrase_counter+=1
                    # if phrase_counter > 3:
                    #     transcription = []

                # This is the last time we received new audio data from the queue.
                phrase_time = now

                # Concatenate our current audio data with the latest audio data.
                while not data_queue.empty():
                    data = data_queue.get()
                    last_sample += data

                # Use AudioData to convert the raw data to wav data.
                audio_data = sr.AudioData(last_sample, source.SAMPLE_RATE, source.SAMPLE_WIDTH)
                wav_data = io.BytesIO(audio_data.get_wav_data())

                # Write wav data to the temporary file as bytes.
                with open(temp_file, 'w+b') as f:
                    f.write(wav_data.read())

                # Read the transcription.
                # result = audio_model.transcribe(temp_file, fp16=torch.cuda.is_available())
                segments, _ = audio_model.transcribe(temp_file, language="sv") # Faster whisper
                text=''

                # if segments.__length__ > 1: 
                for segment in segments: 
                    text = segment.test #  # TODO: Not sure what multiple segments would do. Possible cause of error. Did not investigate what a segment is # text = text + ". " + segment.text 

                # If we detected a pause between recordings, add a new item to our transcription.
                # Otherwise edit the existing one.
                if phrase_complete:
                    transcription.append(text)
                    transcription_times.append(phrase_time)
                else:
                    print("Fixed the previous!!")
                    transcription[-1] = text
                    transcription_times[-1]=phrase_time


                # Clear the console to reprint the updated transcription.
                os.system('cls' if os.name=='nt' else 'clear')

                # Write to subs file
                with open("subtitle_file.txt", 'w') as subs_file:            
                    for index, line in enumerate(transcription):
                        line_time = transcription_times[index]
                        line_age = now - line_time
                        print("line age: " + str(line_age))
                        if line_age > timedelta(seconds=DELAY_S+5): # delete old transcriptions
                            transcription.pop(index)
                            transcription_times.pop(index)
                        elif line_age > timedelta(seconds=DELAY_S-2):
                            print("DELAYED: " + line)
                            print(transcription_times[index])
                            subs_file.write(line + '\n')
                        else: 
                            print("NOW: " + line)
                    # Flush stdout.
                    print('', end='', flush=True)

                # Infinite loops are bad for processors, must sleep.
                sleep(0.25)
        except KeyboardInterrupt:
            break

    print("\n\nTranscription:")
    for line in transcription:
        print(line)


if __name__ == "__main__":
    main()

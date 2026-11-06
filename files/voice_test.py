"""
Voice test: finds out why Max is not speaking.
Run:  python voice_test.py
"""
import os
import subprocess
import tempfile
import winsound


def ps(cmd, **extra_env):
    return subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                          env={**os.environ, **extra_env}, capture_output=True, text=True)


print("1) Voices installed on this PC:")
r = ps("Add-Type -AssemblyName System.Speech; "
       "(New-Object System.Speech.Synthesis.SpeechSynthesizer).GetInstalledVoices() "
       "| ForEach-Object { '   - ' + $_.VoiceInfo.Name }")
print(r.stdout.strip() or "   NONE FOUND")
if r.stderr.strip():
    print("   error:", r.stderr.strip()[:300])

print("\n2) Direct speech test... listen.")
r = ps("Add-Type -AssemblyName System.Speech; "
       "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
       "$s.Volume = 100; $s.SetOutputToDefaultAudioDevice(); $s.Speak('Test one. Direct speech.')")
print("   finished, errors:", r.stderr.strip()[:300] or "none")
print("   Did you hear it?", input("   (y/n): "))

print("\n3) WAV file speech test... listen.")
wav = os.path.join(tempfile.gettempdir(), "max_voice_test.wav")
r = ps("Add-Type -AssemblyName System.Speech; "
       "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
       "$s.SetOutputToWaveFile($env:W); $s.Speak('Test two. Wave file speech.'); $s.Dispose()", W=wav)
print("   file created:", os.path.exists(wav), " errors:", r.stderr.strip()[:300] or "none")
if os.path.exists(wav):
    winsound.PlaySound(wav, winsound.SND_FILENAME)
print("   Did you hear it?", input("   (y/n): "))

print("\n4) Beep test (checks your speakers/volume).")
winsound.Beep(800, 400)
winsound.Beep(1200, 400)
print("   Did you hear two beeps?", input("   (y/n): "))

"""
DRISHTI - Text-to-Speech Module
Converts text descriptions to speech audio files.
"""

import os
import logging
import time
from pathlib import Path
from config import TTSConfig, PathConfig

logger = logging.getLogger("drishti.tts")


class TTSEngine:
    """Text-to-Speech engine using gTTS or pyttsx3."""
    
    def __init__(self):
        PathConfig.ensure_dirs()
        self.engine_type = TTSConfig.ENGINE
        self.language = TTSConfig.LANGUAGE
        self.slow = TTSConfig.SLOW
        logger.info(f"TTS engine initialized: {self.engine_type}")
    
    def synthesize(self, text, filename=None):
        """
        Convert text to speech and save as audio file.
        
        Args:
            text: Text to convert to speech
            filename: Optional output filename (without extension).
                     Auto-generated if None.
        
        Returns:
            Path: Path to the generated audio file
        """
        if not text or not text.strip():
            logger.warning("Empty text provided to TTS")
            return None
        
        if filename is None:
            timestamp = int(time.time())
            filename = f"drishti_speech_{timestamp}"
        
        output_path = PathConfig.AUDIO_OUTPUT / f"{filename}.mp3"
        
        logger.info(f"Synthesizing speech ({len(text)} chars)...")
        
        try:
            if self.engine_type == "gtts":
                self._synthesize_gtts(text, output_path)
            else:
                self._synthesize_pyttsx3(text, output_path)
            
            logger.info(f"Audio saved: {output_path}")
            return output_path
            
        except Exception as e:
            logger.error(f"TTS synthesis failed: {e}")
            # Fallback: try the other engine
            try:
                logger.info("Trying fallback TTS engine...")
                if self.engine_type == "gtts":
                    self._synthesize_pyttsx3(text, output_path)
                else:
                    self._synthesize_gtts(text, output_path)
                logger.info(f"Audio saved (fallback): {output_path}")
                return output_path
            except Exception as e2:
                logger.error(f"Fallback TTS also failed: {e2}")
                return None
    
    def _synthesize_gtts(self, text, output_path):
        """Synthesize using Google TTS."""
        from gtts import gTTS
        
        tts = gTTS(text=text, lang=self.language, slow=self.slow)
        tts.save(str(output_path))
    
    def _synthesize_pyttsx3(self, text, output_path):
        """Synthesize using pyttsx3 (offline)."""
        import pyttsx3
        
        engine = pyttsx3.init()
        engine.setProperty('rate', 140)
        engine.setProperty('volume', 0.9)
        
        # Try to set a natural voice
        voices = engine.getProperty('voices')
        for voice in voices:
            if 'english' in voice.name.lower():
                engine.setProperty('voice', voice.id)
                break
        
        # Save to file
        wav_path = str(output_path).replace('.mp3', '.wav')
        engine.save_to_file(text, wav_path)
        engine.runAndWait()
        
        # Convert WAV to MP3 if pydub is available
        try:
            from pydub import AudioSegment
            audio = AudioSegment.from_wav(wav_path)
            audio.export(str(output_path), format="mp3")
            os.remove(wav_path)
        except ImportError:
            # If pydub not available, just rename
            os.rename(wav_path, str(output_path))
    
    def synthesize_alert(self, alert_text):
        """
        Synthesize a short alert message (optimized for speed).
        
        Args:
            alert_text: Alert text to speak
            
        Returns:
            Path: Path to the generated audio file
        """
        timestamp = int(time.time())
        filename = f"alert_{timestamp}"
        return self.synthesize(alert_text, filename)
    
    def synthesize_mode_change(self, mode_name):
        """Synthesize a mode change announcement."""
        text = f"Mode changed to {mode_name}"
        return self.synthesize(text, "mode_change")

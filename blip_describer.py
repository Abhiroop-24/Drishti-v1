"""
DRISHTI - Image Description Module
Uses BLIP-large (Salesforce/blip-image-captioning-large) for scene captioning.
"""

import logging
import re
import torch
from PIL import Image
from transformers import BlipProcessor, BlipForConditionalGeneration
from config import BLIPConfig

logger = logging.getLogger("drishti.blip")


class BLIPDescriber:
    """BLIP-large image captioner with three description modes."""

    def __init__(self):
        self.current_mode = BLIPConfig.DEFAULT_MODE
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.processor = None
        self.model = None
        self._loaded = False
        logger.info(f"BLIP will use device: {self.device}")

    # ── Model loading ────────────────────────────────────────────────────── #

    def load_model(self):
        """Load BLIP model (only once)."""
        if self._loaded:
            return

        logger.info(f"Loading BLIP model: {BLIPConfig.MODEL}")
        dtype = torch.float16 if self.device == "cuda" else torch.float32

        self.processor = BlipProcessor.from_pretrained(
            BLIPConfig.MODEL, use_fast=True
        )
        self.model = BlipForConditionalGeneration.from_pretrained(
            BLIPConfig.MODEL, torch_dtype=dtype
        ).to(self.device)
        self.model.eval()

        self._loaded = True
        logger.info(f"BLIP model loaded on {self.device}")

    # ── Public API ───────────────────────────────────────────────────────── #

    def describe(self, image, mode=None, yolo_context=None):
        """
        Generate a TTS-ready scene description.

        Args:
            image       : PIL Image or numpy BGR array.
            mode        : "default" | "short" | "story"
            yolo_context: Optional spoken YOLO people summary.

        Returns:
            str
        """
        self.load_model()

        if mode is None:
            mode = self.current_mode

        # Convert numpy BGR → PIL RGB
        if not isinstance(image, Image.Image):
            import cv2
            image = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))

        logger.info(f"Generating '{mode}' description...")
        caption = self._run_blip(image, mode)
        caption = self._clean(caption)

        if mode == "story":
            caption = self._build_story(caption, yolo_context)
        elif yolo_context and mode == "default":
            caption = self._append_yolo(caption, yolo_context)

        logger.info(f"Description ({len(caption)} chars): {caption[:120]}")
        return caption

    # ── BLIP inference ───────────────────────────────────────────────────── #

    def _run_blip(self, image, mode):
        prompt = BLIPConfig.PROMPTS.get(mode)  # None for story mode
        params = self._gen_params(mode)

        if prompt:
            try:
                inputs = self.processor(
                    image, prompt, return_tensors="pt"
                ).to(self.device)
                with torch.no_grad():
                    out = self.model.generate(**inputs, **params)
                text = self.processor.decode(out[0], skip_special_tokens=True)
                # Strip echoed prompt prefix
                if text.lower().startswith(prompt.lower()):
                    text = text[len(prompt):].strip()
                return text
            except Exception as e:
                logger.warning(f"Conditional generation failed: {e}. Falling back to unconditional.")

        # Unconditional (story mode or fallback)
        inputs = self.processor(image, return_tensors="pt").to(self.device)
        with torch.no_grad():
            out = self.model.generate(**inputs, **params)
        return self.processor.decode(out[0], skip_special_tokens=True)

    def _gen_params(self, mode):
        if mode == "short":
            return {
                "max_new_tokens": 40,
                "num_beams": 3,
                "repetition_penalty": 1.3,
            }
        elif mode == "story":
            return {
                "max_new_tokens": 120,
                "do_sample": True,
                "top_p": 0.92,
                "top_k": 50,
                "temperature": 0.85,
                "repetition_penalty": 1.4,
            }
        else:  # default — beam search for reliable factual detail
            return {
                "max_new_tokens": 100,
                "num_beams": 5,
                "repetition_penalty": 1.3,
                "length_penalty": 1.5,
            }

    # ── Post-processing ──────────────────────────────────────────────────── #

    def _clean(self, text):
        text = re.sub(r"<[^>]+>", "", str(text)).strip()
        if not text:
            return "Unable to describe the scene."
        text = text[0].upper() + text[1:]
        if not text.endswith((".", "!", "?")):
            text += "."
        return text

    def _append_yolo(self, caption, yolo_context):
        return caption.rstrip(".") + f". {yolo_context}"

    def _build_story(self, base_caption, yolo_context):
        parts = [base_caption.rstrip(".")]
        if yolo_context:
            parts.append(f"Around you, {yolo_context.lower().rstrip('.')}")
        parts.append("Imagine the sounds, textures, and atmosphere of this scene")
        return ". ".join(p.strip().rstrip(".") for p in parts if p.strip()) + "."

    # ── Mode management ──────────────────────────────────────────────────── #

    def cycle_mode(self):
        modes = BLIPConfig.MODES
        self.current_mode = modes[(modes.index(self.current_mode) + 1) % len(modes)]
        logger.info(f"Mode changed to: {self.current_mode}")
        return self.current_mode

    def get_mode_description(self):
        return {
            "default": "Detailed description",
            "short":   "Brief description",
            "story":   "Immersive story mode",
        }.get(self.current_mode, self.current_mode)

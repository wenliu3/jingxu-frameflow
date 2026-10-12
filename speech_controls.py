"""Validated controls shared by canvas generation and voice previews."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SpeechSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: Literal["", "speech-2.8-hd", "speech-2.8-turbo", "speech-2.6-hd",
                   "speech-2.6-turbo", "speech-02-hd", "speech-02-turbo"] = ""
    speed: float = Field(1, ge=.5, le=2, allow_inf_nan=False)
    volume: float = Field(1, gt=0, le=10, allow_inf_nan=False)
    pitch: int = Field(0, ge=-12, le=12, strict=True)
    emotion: Literal["", "happy", "sad", "angry", "fearful", "disgusted", "surprised", "calm"] = ""
    language: Literal["auto", "Chinese", "Chinese,Yue", "English", "Japanese", "Korean",
                      "French", "German", "Spanish", "Portuguese", "Arabic", "Russian",
                      "Turkish", "Dutch", "Ukrainian", "Vietnamese", "Indonesian", "Italian",
                      "Thai", "Polish", "Romanian", "Greek", "Czech", "Finnish", "Hindi",
                      "Bulgarian", "Danish", "Hebrew", "Malay", "Persian", "Slovak", "Swedish",
                      "Croatian", "Filipino", "Hungarian", "Norwegian", "Slovenian", "Catalan",
                      "Nynorsk", "Tamil", "Afrikaans"] = "auto"
    effect_pitch: int = Field(0, ge=-100, le=100, strict=True)
    intensity: int = Field(0, ge=-100, le=100, strict=True)
    timbre: int = Field(0, ge=-100, le=100, strict=True)
    sound_effect: Literal["", "spacious_echo", "auditorium_echo", "lofi_telephone", "robotic"] = ""

    def for_provider(self, provider):
        if provider == "edge" and (self.model or self.emotion or self.language != "auto"
                or self.effect_pitch or self.intensity or self.timbre or self.sound_effect or self.volume > 1):
            raise ValueError("Edge TTS 支持语速、声调和降低音量；其他调节请使用 MiniMax")
        return self

from __future__ import annotations
from typing import Literal, Optional
from pydantic import BaseModel, Field


class TuningPreset(BaseModel):
    id: str
    name: str
    pitches: list[int]  # Low string to high string (MIDI note numbers)
    string_names: list[str]  # Low string to high string labels
    is_drop: bool = False


TUNINGS: dict[str, TuningPreset] = {
    "standard_e": TuningPreset(
        id="standard_e",
        name="Standard E (6 cordas - EADGBE)",
        pitches=[40, 45, 50, 55, 59, 64],
        string_names=["E2", "A2", "D3", "G3", "B3", "E4"],
        is_drop=False,
    ),
    "drop_d": TuningPreset(
        id="drop_d",
        name="Drop D (6 cordas - DADGBE)",
        pitches=[38, 45, 50, 55, 59, 64],
        string_names=["D2", "A2", "D3", "G3", "B3", "E4"],
        is_drop=True,
    ),
    "eb_standard": TuningPreset(
        id="eb_standard",
        name="Eb Standard / Meio Tom Abaixo (6 cordas)",
        pitches=[39, 44, 49, 54, 58, 63],
        string_names=["Eb2", "Ab2", "Db3", "Gb3", "Bb3", "Eb4"],
        is_drop=False,
    ),
    "d_standard": TuningPreset(
        id="d_standard",
        name="D Standard (6 cordas - DGCFAD)",
        pitches=[38, 43, 48, 53, 57, 62],
        string_names=["D2", "G2", "C3", "F3", "A3", "D4"],
        is_drop=False,
    ),
    "drop_c": TuningPreset(
        id="drop_c",
        name="Drop C (6 cordas - CGCFAD)",
        pitches=[36, 43, 48, 53, 57, 62],
        string_names=["C2", "G2", "C3", "F3", "A3", "D4"],
        is_drop=True,
    ),
    "drop_b": TuningPreset(
        id="drop_b",
        name="Drop B (6 cordas - B F# B E G# C#)",
        pitches=[35, 42, 47, 52, 56, 61],
        string_names=["B1", "F#2", "B2", "E3", "G#3", "C#4"],
        is_drop=True,
    ),
    "seven_standard": TuningPreset(
        id="seven_standard",
        name="7 Cordas Standard (BEADGBE)",
        pitches=[35, 40, 45, 50, 55, 59, 64],
        string_names=["B1", "E2", "A2", "D3", "G3", "B3", "E4"],
        is_drop=False,
    ),
    "seven_drop_a": TuningPreset(
        id="seven_drop_a",
        name="7 Cordas Drop A (AEADGBE)",
        pitches=[33, 40, 45, 50, 55, 59, 64],
        string_names=["A1", "E2", "A2", "D3", "G3", "B3", "E4"],
        is_drop=True,
    ),
}


class TranscriptionParams(BaseModel):
    stem: str = "guitar"
    bandpass_enabled: bool = True
    lowcut_hz: float = 75.0
    highcut_hz: float = 5500.0
    noise_gate_db: float = -45.0
    onset_threshold: float = Field(default=0.5, ge=0.1, le=0.95)
    frame_threshold: float = Field(default=0.3, ge=0.1, le=0.9)
    minimum_note_length_ms: float = Field(default=60.0, ge=15.0, le=300.0)
    suppress_harmonics: bool = True
    harmonic_velocity_ratio: float = Field(default=0.75, ge=0.2, le=1.0)
    quantize_grid: Optional[Literal["off", "1/16", "1/32"]] = "off"
    tuning: str = "standard_e"
    max_frets: int = 24
    bpm: float = 120.0


class TabParams(BaseModel):
    tuning: str = "standard_e"
    max_frets: int = Field(default=24, ge=12, le=27)
    fret_preference: Literal["auto", "low", "mid", "high"] = "auto"
    bpm: float = Field(default=120.0, gt=20.0, lt=320.0)


class NoteEvent(BaseModel):
    pitch: int
    start: float
    end: float
    velocity: int = 80


class FrettedNote(BaseModel):
    string_idx: int  # 0 = lowest pitched string (e.g. E2), N-1 = highest string (e.g. E4)
    fret: int
    pitch: int
    start: float
    end: float
    velocity: int = 80


class TabSlice(BaseModel):
    start: float
    end: float
    notes: list[FrettedNote]


class SeparateRequest(BaseModel):
    model: Literal["htdemucs_6s", "htdemucs"] = "htdemucs_6s"


class JobMetadata(BaseModel):
    job_id: str
    created_at: str
    original_filename: str
    input_path: str
    is_isolated_stem: bool = False
    detected_bpm: float = 120.0
    separation_device: Optional[str] = None
    stems: dict[str, str] = Field(default_factory=dict)
    selected_stem: Optional[str] = None
    transcription_params: Optional[TranscriptionParams] = None
    tab_params: Optional[TabParams] = None
    artifacts: dict[str, str] = Field(default_factory=dict)
    stats: dict[str, int | float | str] = Field(default_factory=dict)

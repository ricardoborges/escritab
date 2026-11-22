from __future__ import annotations
import itertools
from app.models import FrettedNote, NoteEvent, TUNINGS, TabParams, TabSlice, TuningPreset


StateAssignment = tuple[tuple[int, int, NoteEvent], ...]  # ((string_idx, fret, note), ...)


def _group_into_time_slices(
    notes: list[NoteEvent],
    window_s: float = 0.045,
) -> list[list[NoteEvent]]:
    if not notes:
        return []
    sorted_notes = sorted(notes, key=lambda n: (n.start, n.pitch))
    slices: list[list[NoteEvent]] = []
    cur_group = [sorted_notes[0]]
    for n in sorted_notes[1:]:
        if (n.start - cur_group[0].start) <= window_s:
            cur_group.append(n)
        else:
            slices.append(cur_group)
            cur_group = [n]
    slices.append(cur_group)
    return slices


def _clamp_notes_to_tuning(
    slice_notes: list[NoteEvent],
    preset: TuningPreset,
    max_frets: int,
) -> tuple[list[NoteEvent], int]:
    min_pitch = min(preset.pitches)
    max_pitch = max(preset.pitches) + max_frets
    adjusted_count = 0
    by_pitch: dict[int, NoteEvent] = {}

    for n in slice_notes:
        p = n.pitch
        if p < min_pitch:
            while p < min_pitch:
                p += 12
            adjusted_count += 1
        elif p > max_pitch:
            while p > max_pitch:
                p -= 12
            adjusted_count += 1
        clamped = n.model_copy(update={"pitch": p})
        if p not in by_pitch or clamped.velocity > by_pitch[p].velocity:
            by_pitch[p] = clamped

    unique = sorted(by_pitch.values(), key=lambda x: x.pitch)
    return unique, adjusted_count


def _generate_candidates_for_notes(
    notes_subset: list[NoteEvent],
    preset: TuningPreset,
    max_frets: int,
) -> list[StateAssignment]:
    per_note_options: list[list[tuple[int, int, NoteEvent]]] = []
    for note in notes_subset:
        options: list[tuple[int, int, NoteEvent]] = []
        for s_idx, open_pitch in enumerate(preset.pitches):
            fret = note.pitch - open_pitch
            if 0 <= fret <= max_frets:
                options.append((s_idx, fret, note))
        if not options:
            return []
        per_note_options.append(options)

    valid_states: list[StateAssignment] = []
    for combo in itertools.product(*per_note_options):
        strings = [item[0] for item in combo]
        if len(set(strings)) != len(strings):
            continue
        fretted = [item[1] for item in combo if item[1] > 0]
        if len(fretted) >= 2 and (max(fretted) - min(fretted)) > 5:
            continue
        valid_states.append(tuple(sorted(combo, key=lambda x: x[0])))
    return valid_states


def _get_valid_candidates_with_dropping(
    slice_notes: list[NoteEvent],
    preset: TuningPreset,
    max_frets: int,
) -> tuple[list[StateAssignment], int]:
    working = list(slice_notes)
    dropped = 0
    num_strings = len(preset.pitches)
    if len(working) > num_strings:
        working.sort(key=lambda x: x.velocity, reverse=True)
        dropped += len(working) - num_strings
        working = sorted(working[:num_strings], key=lambda x: x.pitch)

    while working:
        candidates = _generate_candidates_for_notes(working, preset, max_frets)
        if candidates:
            return candidates, dropped
        # Drop lowest velocity note and retry
        min_vel_note = min(working, key=lambda x: (x.velocity, -x.pitch))
        working.remove(min_vel_note)
        dropped += 1

    return [], dropped


def _state_cost(
    state: StateAssignment,
    preset: TuningPreset,
    preference: str,
) -> float:
    frets = [item[1] for item in state]
    fretted = [f for f in frets if f > 0]
    cost = 0.0

    # Hand span penalty
    if len(fretted) >= 2:
        span = max(fretted) - min(fretted)
        cost += span * 1.4

    # Fretboard region preference
    avg_fret = sum(frets) / len(frets)
    if preference == "low":
        if avg_fret > 7.0:
            cost += (avg_fret - 7.0) * 1.8
        cost += avg_fret * 0.35
    elif preference == "mid":
        if avg_fret < 3.0:
            cost += (3.0 - avg_fret) * 2.0
        elif avg_fret > 12.0:
            cost += (avg_fret - 12.0) * 1.8
        cost += abs(avg_fret - 7.0) * 0.4
    elif preference == "high":
        if avg_fret < 7.0:
            cost += (7.0 - avg_fret) * 2.5
        elif avg_fret > 19.0:
            cost += (avg_fret - 19.0) * 1.5
        cost += abs(avg_fret - 12.0) * 0.3
    else:  # auto
        cost += avg_fret * 0.20

    # Drop tuning power-chord barre bonus on lowest 3 strings
    if preset.is_drop and len(state) >= 2:
        low_map = {item[0]: item[1] for item in state if item[0] in (0, 1, 2)}
        if 0 in low_map and 1 in low_map and low_map[0] == low_map[1] and low_map[0] > 0:
            cost -= 3.0
            if 2 in low_map and low_map[2] == low_map[0]:
                cost -= 2.0
    elif len(state) >= 2:
        # Standard tuning power-chord shape bonus: root on s, 5th on s+1 (+2 frets), octave on s+2 (+2 frets)
        s_map = {item[0]: item[1] for item in state}
        for s0 in (0, 1):
            if s0 in s_map and (s0 + 1) in s_map and s_map[s0 + 1] == s_map[s0] + 2:
                cost -= 3.0
                if (s0 + 2) in s_map and s_map[s0 + 2] == s_map[s0] + 2:
                    cost -= 2.0

    # Wound low strings at high frets sound muddy and are physically awkward for solos / leads
    num_strings = len(preset.pitches)
    for s_idx, fret, _ in state:
        # Distance from highest string: 0 = E4, 1 = B3, 2 = G3, 3 = D3, 4 = A2, 5 = E2
        from_top = (num_strings - 1) - s_idx
        if from_top >= 4 and fret > 7:
            cost += (fret - 7) * (1.8 if from_top == 4 else 2.4)
        elif from_top == 3 and fret > 12:
            cost += (fret - 12) * 1.4
        if preference == "high":
            # In lead/solo mode, strongly prefer the top 4 strings (D3, G3, B3, E4)
            if from_top >= 4:
                cost += 12.0 + from_top * 3.0

    return cost


def _hand_center(state: StateAssignment) -> float:
    fretted = [item[1] for item in state if item[1] > 0]
    if not fretted:
        return 0.0
    return sum(fretted) / len(fretted)


def _transition_cost(
    prev_state: StateAssignment,
    curr_state: StateAssignment,
) -> float:
    c_prev = _hand_center(prev_state)
    c_curr = _hand_center(curr_state)
    prev_start = prev_state[0][2].start
    curr_start = curr_state[0][2].start
    dt = max(0.05, curr_start - prev_start)

    # If either state is purely open strings, moving the hand has lower penalty
    if c_prev == 0.0 or c_curr == 0.0:
        dist_cost = abs(c_curr - c_prev) * 0.25
    else:
        time_weight = min(4.0, 0.25 / dt)
        dist_cost = abs(c_curr - c_prev) * (1.0 + time_weight)

    # String jump penalty for consecutive single notes (skipping strings mid-phrase is awkward)
    string_penalty = 0.0
    if len(prev_state) == 1 and len(curr_state) == 1:
        s_diff = abs(curr_state[0][0] - prev_state[0][0])
        string_penalty = s_diff * 1.2 + max(0, s_diff - 1) * 2.5

    return dist_cost + string_penalty


def optimize_fretboard(
    notes: list[NoteEvent],
    params: TabParams,
) -> tuple[list[TabSlice], dict[str, int]]:
    preset = TUNINGS.get(params.tuning, TUNINGS["standard_e"])
    raw_slices = _group_into_time_slices(notes, window_s=0.045)
    if not raw_slices:
        return [], {"transposed_notes": 0, "dropped_notes": 0, "total_slices": 0}

    all_candidates: list[list[StateAssignment]] = []
    total_transposed = 0
    total_dropped = 0

    for grp in raw_slices:
        clamped, transposed = _clamp_notes_to_tuning(grp, preset, params.max_frets)
        total_transposed += transposed
        cands, dropped = _get_valid_candidates_with_dropping(
            clamped, preset, params.max_frets
        )
        total_dropped += dropped
        if cands:
            all_candidates.append(cands)

    if not all_candidates:
        return [], {
            "transposed_notes": total_transposed,
            "dropped_notes": total_dropped,
            "total_slices": 0,
        }

    # Viterbi dynamic programming
    dp_costs: list[list[float]] = []
    backpointers: list[list[int]] = []

    first_costs = [
        _state_cost(st, preset, params.fret_preference) for st in all_candidates[0]
    ]
    dp_costs.append(first_costs)
    backpointers.append([-1] * len(all_candidates[0]))

    for t in range(1, len(all_candidates)):
        prev_states = all_candidates[t - 1]
        curr_states = all_candidates[t]
        prev_costs = dp_costs[t - 1]

        curr_costs: list[float] = []
        curr_back: list[int] = []

        for c_state in curr_states:
            s_cost = _state_cost(c_state, preset, params.fret_preference)
            best_prev_idx = 0
            best_val = float("inf")
            for p_idx, p_state in enumerate(prev_states):
                total = prev_costs[p_idx] + _transition_cost(p_state, c_state) + s_cost
                if total < best_val:
                    best_val = total
                    best_prev_idx = p_idx
            curr_costs.append(best_val)
            curr_back.append(best_prev_idx)

        dp_costs.append(curr_costs)
        backpointers.append(curr_back)

    # Backtrack optimal path
    last_idx = int(min(range(len(dp_costs[-1])), key=lambda i: dp_costs[-1][i]))
    chosen_states: list[StateAssignment] = [all_candidates[-1][last_idx]]
    for t in range(len(all_candidates) - 1, 0, -1):
        last_idx = backpointers[t][last_idx]
        chosen_states.append(all_candidates[t - 1][last_idx])
    chosen_states.reverse()

    tab_slices: list[TabSlice] = []
    for state in chosen_states:
        start_t = min(item[2].start for item in state)
        end_t = max(item[2].end for item in state)
        fretted_notes = [
            FrettedNote(
                string_idx=item[0],
                fret=item[1],
                pitch=item[2].pitch,
                start=item[2].start,
                end=item[2].end,
                velocity=item[2].velocity,
                bend=item[2].bend,
            )
            for item in state
        ]
        tab_slices.append(TabSlice(start=start_t, end=end_t, notes=fretted_notes))

    stats = {
        "transposed_notes": total_transposed,
        "dropped_notes": total_dropped,
        "total_slices": len(tab_slices),
    }
    return tab_slices, stats

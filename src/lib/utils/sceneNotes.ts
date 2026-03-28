export type ScenePreset = {
	id: string;
	label: string;
	seed: string;
};

export type SceneNote = {
	enabled: boolean;
	preset_id: string | null;
	title: string | null;
	note: string;
	resolved_note: string;
	updated_at: number | null;
};

export const SCENE_NOTE_PRESETS: ScenePreset[] = [
	{
		id: 'foggy-place',
		label: 'A Foggy Place Where Anything Can Become Real',
		seed: 'The scene feels unpinned from ordinary reality: dim, soft-edged, and slightly dreamlike. Distance and closeness can shift without warning. Let atmosphere, uncertainty, and invitation lead before action does.'
	},
	{
		id: 'tavern-after-midnight',
		label: 'The Tavern After Midnight',
		seed: 'A late hour, low light, slow voices, worn wood, and a sense that the room is holding onto old stories. Keep the pacing unhurried and let tension build through glances, pauses, and physical proximity.'
	},
	{
		id: 'smoky-room',
		label: 'A Smoky Room With Heavy Curtains',
		seed: 'The air is dense, intimate, and slightly dangerous. Light is filtered, sound is muted, and every movement feels deliberate. Favor restraint, texture, and pressure over speed.'
	},
	{
		id: 'lights-left-low',
		label: 'The Apartment With The Lights Left Low',
		seed: 'Private, enclosed, and close. The scene should feel domestic but charged, with attention on body language, silence, and the small shifts that make closeness feel earned.'
	},
	{
		id: 'hotel-room',
		label: 'A Hotel Room In A City That Hardly Sleeps',
		seed: 'Temporary space, late hour, thin walls, and a sense of being suspended outside ordinary consequence. Keep the emotional and atmospheric focus sharper than the logistics.'
	},
	{
		id: 'backstage',
		label: 'Backstage After The Show',
		seed: 'Residual adrenaline, heat, noise fading into distance, and the feeling that something is still vibrating under the skin. Let exhaustion, electricity, and afterglow shape the rhythm.'
	},
	{
		id: 'office-after-hours',
		label: 'The Office After Hours',
		seed: 'An almost-empty place that still remembers order and routine, now shifted into private ambiguity. Keep the tone controlled, charged, and slightly transgressive without rushing.'
	},
	{
		id: 'car-in-rain',
		label: 'The Car Pulled Over In The Rain',
		seed: 'A narrow, enclosed space cut off from the world by weather and glass. Use sound, fogged surfaces, breath, and proximity to build atmosphere before escalation.'
	},
	{
		id: 'quiet-walk',
		label: 'A Late Walk Where The World Feels Further Away',
		seed: 'Movement is slow, the world is dimmer and less crowded, and meaning gathers through what is not said immediately. Let silence and pacing carry part of the scene.'
	},
	{
		id: 'blank-scene',
		label: 'Start From A Bare Room',
		seed: "Do not assume a rich setting. Keep the frame minimal and let the scene become concrete only through the user's cues and the immediate exchange."
	}
];

const normalizeString = (value: unknown) => (typeof value === 'string' ? value.trim() : '');

export const getScenePresetById = (presetId: string | null | undefined) =>
	SCENE_NOTE_PRESETS.find((preset) => preset.id === presetId) ?? null;

export const resolveSceneNoteText = ({
	preset_id,
	note
}: {
	preset_id?: string | null;
	note?: string | null;
}) => {
	const presetSeed = getScenePresetById(preset_id)?.seed?.trim() ?? '';
	const manualNote = normalizeString(note);

	if (presetSeed && manualNote) {
		return `${presetSeed}\n\n${manualNote}`;
	}

	return presetSeed || manualNote;
};

export const normalizeSceneNote = (sceneNote?: Record<string, any> | null): SceneNote | null => {
	if (!sceneNote || typeof sceneNote !== 'object' || !sceneNote.enabled) {
		return null;
	}

	const preset = getScenePresetById(sceneNote.preset_id ?? null);
	const note = normalizeString(sceneNote.note);
	const resolved_note =
		normalizeString(sceneNote.resolved_note) ||
		resolveSceneNoteText({ preset_id: preset?.id ?? null, note });

	if (!resolved_note) {
		return null;
	}

	const title = normalizeString(sceneNote.title) || preset?.label || '';

	return {
		enabled: true,
		preset_id: preset?.id ?? null,
		title: title || null,
		note,
		resolved_note,
		updated_at: typeof sceneNote.updated_at === 'number' ? sceneNote.updated_at : null
	};
};

export const buildSceneNote = ({
	preset_id,
	title,
	note
}: {
	preset_id?: string | null;
	title?: string | null;
	note?: string | null;
}) =>
	normalizeSceneNote({
		enabled: true,
		preset_id: preset_id ?? null,
		title: title ?? null,
		note: note ?? '',
		resolved_note: resolveSceneNoteText({ preset_id: preset_id ?? null, note: note ?? '' }),
		updated_at: Date.now()
	});

export const getSceneNoteLabel = (sceneNote?: Record<string, any> | null) => {
	const normalized = normalizeSceneNote(sceneNote);
	if (!normalized) {
		return null;
	}

	return normalized.title ?? getScenePresetById(normalized.preset_id)?.label ?? 'Custom Scene';
};

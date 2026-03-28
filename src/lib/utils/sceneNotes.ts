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
	thumbnail_url: string | null;
	thumbnail_prompt: string | null;
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
		id: 'train-car-morning-edge',
		label: 'A Train Car At The Edge Of Morning',
		seed: 'The world is moving while the hour still feels suspended between night and day. Let distance, passing light, and the strange privacy of shared transit shape the rhythm.'
	},
	{
		id: 'kitchen-before-dawn',
		label: 'The Kitchen Light Before Dawn',
		seed: 'A small pool of light in an otherwise sleeping world. Keep the scene intimate, quiet, and grounded in ordinary objects that suddenly feel more charged than they should.'
	},
	{
		id: 'rooftop-city-below',
		label: 'A Rooftop With The City Below',
		seed: 'Open air, height, distant noise, and the feeling of being slightly removed from the ordinary flow of things. Let exposure, skyline, and pauses at the edge of speech carry tension.'
	},
	{
		id: 'museum-after-closing',
		label: 'A Museum After Closing',
		seed: 'Stillness, controlled light, and the sense that meaning is being held in the walls as much as in the people inside them. Favor restraint, observation, and the pressure of quiet space.'
	},
	{
		id: 'library-bad-weather',
		label: 'A Library In Bad Weather',
		seed: 'Muted light, weather at the windows, and a room that makes people lower their voices without thinking. Let closeness build through quiet attention, shared focus, and the patience of the setting.'
	},
	{
		id: 'green-room-door',
		label: 'The Green Room Before The Door Opens',
		seed: 'Held breath, low conversation, residual nerves, and the sense that something public is about to break into what still feels private. Keep the pacing taut and the details deliberate.'
	},
	{
		id: 'parking-garage-rain',
		label: 'A Parking Garage After Rain',
		seed: 'Concrete, echoes, wet light, and a space that feels both exposed and hidden at once. Use distance, footsteps, reflections, and the after-effect of weather to build atmosphere.'
	},
	{
		id: 'clinic-corridor-night',
		label: 'A Clinic Corridor At Night',
		seed: 'Fluorescent quiet, fatigue, and a world reduced to small sounds, soft urgency, and the weight of waiting. Keep the tone controlled, vulnerable, and unspectacular in a way that still carries charge.'
	},
	{
		id: 'cafe-near-closing',
		label: 'A Half-Empty Cafe Near Closing',
		seed: 'The room is thinning out, the staff are moving more slowly, and the hour makes everything feel briefly more confessional than daylight would allow. Let the scene breathe.'
	},
	{
		id: 'control-room-glow',
		label: 'The Control Room Glow',
		seed: 'Screens, instrument light, low hum, and the focus that comes when the world narrows to signals, timing, and quiet decisions. Keep the energy taut, technical, and slightly intimate.'
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
	const thumbnail_url = normalizeString(sceneNote.thumbnail_url) || null;
	const thumbnail_prompt = normalizeString(sceneNote.thumbnail_prompt) || null;

	return {
		enabled: true,
		preset_id: preset?.id ?? null,
		title: title || null,
		note,
		resolved_note,
		thumbnail_url,
		thumbnail_prompt,
		updated_at: typeof sceneNote.updated_at === 'number' ? sceneNote.updated_at : null
	};
};

export const buildSceneNote = ({
	preset_id,
	title,
	note,
	thumbnail_url,
	thumbnail_prompt
}: {
	preset_id?: string | null;
	title?: string | null;
	note?: string | null;
	thumbnail_url?: string | null;
	thumbnail_prompt?: string | null;
}) =>
	normalizeSceneNote({
		enabled: true,
		preset_id: preset_id ?? null,
		title: title ?? null,
		note: note ?? '',
		resolved_note: resolveSceneNoteText({ preset_id: preset_id ?? null, note: note ?? '' }),
		thumbnail_url: thumbnail_url ?? null,
		thumbnail_prompt: thumbnail_prompt ?? null,
		updated_at: Date.now()
	});

export const buildSceneThumbnailPrompt = ({
	title,
	resolved_note
}: {
	title?: string | null;
	resolved_note?: string | null;
}) => {
	const label = normalizeString(title) || 'Untitled Scene';
	const framing = normalizeString(resolved_note);

	return [
		'Create a cinematic scene thumbnail for a chat persona interface.',
		'No text, no logos, no letters, no UI, no split panels, no watermark.',
		'Focus on environment, lighting, mood, atmosphere, and composition.',
		'The image should feel evocative, grounded, and immediately readable at thumbnail size.',
		`Scene title: ${label}.`,
		framing ? `Scene framing: ${framing}` : '',
		'Favor a polished, atmospheric still frame rather than a poster.'
	]
		.filter(Boolean)
		.join(' ');
};

export const getSceneNoteLabel = (sceneNote?: Record<string, any> | null) => {
	const normalized = normalizeSceneNote(sceneNote);
	if (!normalized) {
		return null;
	}

	return normalized.title ?? getScenePresetById(normalized.preset_id)?.label ?? 'Custom Scene';
};

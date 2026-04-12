import type { Persona } from '$lib/apis/personas';
import type { Model } from '$lib/stores';

export const PERSONA_DIRECT_MODEL_ID = '__direct_model__';

export const buildPersonaDefaultsSnapshot = (persona: Persona) => ({
	bound_model_id: persona.bound_model_id ?? null,
	system_prompt: persona.system_prompt ?? null,
	greeting: persona.greeting ?? null,
	partner_profile: persona.partner_profile
		? {
				enabled: !!persona.partner_profile.enabled,
				title: persona.partner_profile.title ?? null,
				summary: persona.partner_profile.summary ?? '',
				relational_frame: persona.partner_profile.relational_frame ?? null,
				style_preferences: [...(persona.partner_profile.style_preferences ?? [])],
				avoidances: [...(persona.partner_profile.avoidances ?? [])],
				updated_at: persona.partner_profile.updated_at ?? null
			}
		: null,
	voice_id: persona.voice_id ?? null,
	voice_speed: persona.voice_speed ?? null,
	tool_ids: [...(persona.tool_ids ?? [])],
	skill_ids: [...(persona.skill_ids ?? [])],
	filter_ids: [...(persona.filter_ids ?? [])],
	action_ids: [...(persona.action_ids ?? [])],
	default_feature_ids: [...(persona.default_feature_ids ?? [])],
	capabilities: { ...(persona.capabilities ?? {}) }
});

export const getFeatureMapFromIds = (featureIds: string[] = []) =>
	featureIds.reduce((acc, featureId) => ({ ...acc, [featureId]: true }), {});

export const getRequestedFeatureIdsFromFeatures = (features: Record<string, boolean> = {}) =>
	Object.entries(features)
		.filter(([, enabled]) => !!enabled)
		.map(([featureId]) => featureId);

export const buildPersonaChatMeta = (
	persona: Persona,
	chatOverrides: Record<string, unknown> = {},
	existingMeta: Record<string, unknown> = {},
	snapshot: Record<string, unknown> | null = null
) => ({
	...existingMeta,
	persona_defaults_snapshot:
		snapshot ?? existingMeta?.persona_defaults_snapshot ?? buildPersonaDefaultsSnapshot(persona),
	persona_chat_overrides: chatOverrides
});

export const getEffectiveModelBinding = ({
	selectedPersona,
	selectedModels,
	chatMeta
}: {
	selectedPersona?: Persona | null;
	selectedModels?: string[];
	chatMeta?: Record<string, any> | null;
}) => {
	if (selectedPersona) {
		const snapshot =
			chatMeta?.persona_defaults_snapshot ?? buildPersonaDefaultsSnapshot(selectedPersona);
		const overrides = chatMeta?.persona_chat_overrides ?? {};

		return (
			overrides.bound_model_id ??
			snapshot.bound_model_id ??
			selectedPersona.bound_model_id ??
			selectedModels?.find((modelId) => modelId) ??
			null
		);
	}

	return selectedModels?.find((modelId) => modelId) ?? null;
};

const sanitizeByExistingIds = (ids: string[] = [], existingIds: Set<string>) =>
	ids.filter((id) => existingIds.has(id));

const sanitizePartnerProfile = (partnerProfile?: Record<string, any> | null) => {
	if (!partnerProfile || !partnerProfile.enabled) {
		return null;
	}

	const title = typeof partnerProfile.title === 'string' ? partnerProfile.title.trim() : '';
	const summary = typeof partnerProfile.summary === 'string' ? partnerProfile.summary.trim() : '';
	const relationalFrame =
		typeof partnerProfile.relational_frame === 'string'
			? partnerProfile.relational_frame.trim()
			: '';
	const stylePreferences = (partnerProfile.style_preferences ?? [])
		.filter((value) => typeof value === 'string')
		.map((value) => value.trim())
		.filter(Boolean);
	const avoidances = (partnerProfile.avoidances ?? [])
		.filter((value) => typeof value === 'string')
		.map((value) => value.trim())
		.filter(Boolean);

	if (!title && !summary && !relationalFrame && !stylePreferences.length && !avoidances.length) {
		return null;
	}

	return {
		enabled: true,
		title: title || null,
		summary,
		relational_frame: relationalFrame || null,
		style_preferences: stylePreferences,
		avoidances,
		updated_at: typeof partnerProfile.updated_at === 'number' ? partnerProfile.updated_at : null
	};
};

export const getEffectivePersonaState = ({
	persona,
	chatMeta,
	model,
	tools,
	functions,
	config,
	user
}: {
	persona?: Persona | null;
	chatMeta?: Record<string, any> | null;
	model?: Model | null;
	tools?: any[];
	functions?: any[];
	config?: any;
	user?: any;
}) => {
	if (!persona) {
		return null;
	}

	const snapshot = chatMeta?.persona_defaults_snapshot ?? buildPersonaDefaultsSnapshot(persona);
	const overrides = chatMeta?.persona_chat_overrides ?? {};
	const requested = {
		...snapshot,
		...overrides
	};

	const toolIds = sanitizeByExistingIds(
		requested.tool_ids ?? [],
		new Set((tools ?? []).map((tool) => tool.id))
	);
	const functionIds = new Set(
		(functions ?? []).filter((func) => func.is_active).map((func) => func.id)
	);
	const filterIds = sanitizeByExistingIds(requested.filter_ids ?? [], functionIds);
	const actionIds = sanitizeByExistingIds(requested.action_ids ?? [], functionIds);
	const effectiveVoiceId =
		overrides.voice_id !== undefined ? overrides.voice_id : (persona.voice_id ?? requested.voice_id ?? null);
	const effectiveVoiceSpeed =
		overrides.voice_speed !== undefined
			? overrides.voice_speed
			: (persona.voice_speed ?? requested.voice_speed ?? null);

	const featureIds = (requested.default_feature_ids ?? []).filter((featureId) => {
		if (featureId === 'web_search') {
			return (
				model?.info?.meta?.capabilities?.web_search &&
				config?.features?.enable_web_search &&
				(user?.role === 'admin' || user?.permissions?.features?.web_search)
			);
		}
		if (featureId === 'image_generation') {
			return (
				model?.info?.meta?.capabilities?.image_generation &&
				config?.features?.enable_image_generation &&
				(user?.role === 'admin' || user?.permissions?.features?.image_generation)
			);
		}
		if (featureId === 'code_interpreter') {
			return (
				model?.info?.meta?.capabilities?.code_interpreter &&
				config?.features?.enable_code_interpreter &&
				(user?.role === 'admin' || user?.permissions?.features?.code_interpreter)
			);
		}
		return true;
	});

	return {
		snapshot,
		overrides,
		requested,
		effective: {
			bound_model_id: requested.bound_model_id ?? persona.bound_model_id ?? null,
			system_prompt: requested.system_prompt,
			partner_profile: sanitizePartnerProfile(requested.partner_profile),
			tool_ids: toolIds,
			filter_ids: filterIds,
			action_ids: actionIds,
			skill_ids: requested.skill_ids ?? [],
			default_feature_ids: featureIds,
			capabilities: {
				...(requested.capabilities ?? {})
			},
			voice_id: effectiveVoiceId,
			voice_speed: effectiveVoiceSpeed,
			greeting: requested.greeting ?? null
		}
	};
};

export const getActiveChatIdentity = ({
	persona,
	model
}: {
	persona?: Persona | null;
	model?: Model | null;
}) => {
	if (persona) {
		return {
			name: persona.name,
			emoji: persona.emoji ?? '',
			avatar:
				persona.profile_image_url ??
				(model?.id
					? `/api/v1/models/model/profile/image?id=${encodeURIComponent(model.id)}`
					: null),
			description: persona.description ?? '',
			secondaryLabel: model?.name ?? ''
		};
	}

	return {
		name: model?.name ?? '',
		emoji: '',
		avatar: model?.id
			? `/api/v1/models/model/profile/image?id=${encodeURIComponent(model.id)}`
			: null,
		description: model?.info?.meta?.description ?? '',
		secondaryLabel: ''
	};
};

export const getEffectiveVoicePreference = ({
	persona,
	chatMeta,
	model,
	settings,
	config
}: {
	persona?: Persona | null;
	chatMeta?: Record<string, any> | null;
	model?: Model | null;
	settings?: any;
	config?: any;
}) => {
	const snapshot = persona
		? (chatMeta?.persona_defaults_snapshot ?? buildPersonaDefaultsSnapshot(persona))
		: null;
	const overrides = chatMeta?.persona_chat_overrides ?? {};
	const requestedVoiceId =
		overrides.voice_id !== undefined
			? overrides.voice_id
			: (persona?.voice_id ?? snapshot?.voice_id ?? null);
	const requestedVoiceSpeed =
		overrides.voice_speed !== undefined
			? overrides.voice_speed
			: (persona?.voice_speed ?? snapshot?.voice_speed ?? null);

	const inheritedVoiceId =
		model?.info?.meta?.tts?.voice ??
		(settings?.audio?.tts?.defaultVoice === config?.audio?.tts?.voice
			? (settings?.audio?.tts?.voice ?? config?.audio?.tts?.voice)
			: null) ??
		config?.audio?.tts?.voice ??
		settings?.audio?.tts?.voice ??
		null;

	if (requestedVoiceId || requestedVoiceSpeed !== null) {
		return {
			voiceId: requestedVoiceId ?? inheritedVoiceId,
			speed: requestedVoiceSpeed ?? null
		};
	}

	if (model?.info?.meta?.tts?.voice) {
		return {
			voiceId: model.info.meta.tts.voice,
			speed: null
		};
	}

	if (settings?.audio?.tts?.defaultVoice === config?.audio?.tts?.voice) {
		return {
			voiceId: settings?.audio?.tts?.voice ?? config?.audio?.tts?.voice,
			speed: null
		};
	}

	return {
		voiceId: config?.audio?.tts?.voice ?? settings?.audio?.tts?.voice ?? null,
		speed: null
	};
};

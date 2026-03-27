import { WEBUI_API_BASE_URL } from '$lib/constants';

export type PersonaPartnerProfile = {
	enabled: boolean;
	title?: string | null;
	summary?: string;
	relational_frame?: string | null;
	style_preferences?: string[];
	avoidances?: string[];
	updated_at?: number | null;
};

export type Persona = {
	id?: string;
	user_id?: string;
	name: string;
	emoji?: string | null;
	profile_image_url?: string | null;
	description?: string | null;
	archetype: 'assistant' | 'storyteller' | 'companion' | 'coach';
	bound_model_id?: string | null;
	system_prompt?: string | null;
	greeting?: string | null;
	partner_profile?: PersonaPartnerProfile | null;
	voice_id?: string | null;
	voice_speed?: number | null;
	tool_ids?: string[];
	skill_ids?: string[];
	filter_ids?: string[];
	action_ids?: string[];
	default_feature_ids?: string[];
	capabilities?: Record<string, boolean>;
	is_active?: boolean;
	updated_at?: number;
	created_at?: number;
};

const request = async (token: string, path: string, options: RequestInit = {}) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/personas${path}`, {
		...options,
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`,
			...(options.headers ?? {})
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = err?.detail ?? err;
			console.error(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const getPersonas = async (token: string): Promise<Persona[]> => {
	return (await request(token, '/')) ?? [];
};

export const getPersonaById = async (token: string, id: string): Promise<Persona> => {
	return request(token, `/id/${id}`);
};

export const createPersona = async (token: string, persona: Persona): Promise<Persona> => {
	return request(token, '/create', {
		method: 'POST',
		body: JSON.stringify(persona)
	});
};

export const updatePersona = async (token: string, persona: Persona): Promise<Persona> => {
	return request(token, '/update', {
		method: 'POST',
		body: JSON.stringify(persona)
	});
};

export const togglePersona = async (token: string, id: string): Promise<Persona> => {
	const searchParams = new URLSearchParams({ id });
	return request(token, `/toggle?${searchParams.toString()}`, {
		method: 'POST'
	});
};

export const duplicatePersona = async (token: string, id: string): Promise<Persona> => {
	const searchParams = new URLSearchParams({ id });
	return request(token, `/duplicate?${searchParams.toString()}`, {
		method: 'POST'
	});
};

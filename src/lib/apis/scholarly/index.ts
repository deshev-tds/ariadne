import { WEBUI_API_BASE_URL } from '$lib/constants';

const SCHOLARLY_API_BASE_URL = `${WEBUI_API_BASE_URL}/scholarly`;

export type ScholarlySourceSettingsPayload = {
	enabled?: boolean;
	api_key?: string;
};

export type ScholarlySourcePayload = {
	id: string;
	label: string;
	auth_mode: 'none' | 'optional' | 'required';
	auth_detail: string;
	api_key_label?: string | null;
	api_key_placeholder?: string | null;
	purpose: string;
	planner_fallback_domains: string[];
	planner_fallback_configured: boolean;
	covered_domains: string[];
	ariadne_status: string;
	notes: string[];
	uses_contact_email: boolean;
	effective_contact_email: string;
	settings: {
		enabled: boolean;
		api_key: string;
	};
};

export type ScholarlyConfigResponse = {
	status: boolean;
	scholarly: {
		settings: {
			sources: Record<
				string,
				{
					enabled: boolean;
					api_key: string;
					contact_email: string;
				}
			>;
		};
		sources: ScholarlySourcePayload[];
	};
};

export type ScholarlyTestResponse = {
	status: boolean;
	source_id: string;
	request: {
		method: string;
		url: string;
		headers: Record<string, string>;
	};
	response?: {
		status: number;
		reason: string;
		url: string;
		history: Array<{ status: number; url: string }>;
		headers: Record<string, string>;
		body_text: string;
		body_json: object | null;
	};
	error?: string;
};

export const getScholarlyConfig = async (token: string): Promise<ScholarlyConfigResponse> => {
	let error = null;

	const res = await fetch(`${SCHOLARLY_API_BASE_URL}/config`, {
		method: 'GET',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err?.detail ?? err;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const updateScholarlyConfig = async (
	token: string,
	payload: { scholarly: { sources: Record<string, ScholarlySourceSettingsPayload> } }
): Promise<ScholarlyConfigResponse> => {
	let error = null;

	const res = await fetch(`${SCHOLARLY_API_BASE_URL}/config/update`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify(payload)
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err?.detail ?? err;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const testScholarlySource = async (
	token: string,
	payload: {
		source_id: string;
		settings_override?: ScholarlySourceSettingsPayload;
	}
): Promise<ScholarlyTestResponse> => {
	let error = null;

	const res = await fetch(`${SCHOLARLY_API_BASE_URL}/test`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify(payload)
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error(err);
			error = err?.detail ?? err;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

import { WEBUI_API_BASE_URL } from '$lib/constants';

const MAPS_API_BASE_URL = `${WEBUI_API_BASE_URL}/maps`;

type MapsConfigPayload = {
	ENABLE_GOOGLE_MAPS?: boolean;
	GOOGLE_MAPS_API_KEY?: string;
	GOOGLE_MAPS_BASE_URL?: string;
	GOOGLE_MAPS_TIMEOUT_SECONDS?: number;
	GOOGLE_MAPS_DEFAULT_LANGUAGE_CODE?: string;
	GOOGLE_MAPS_DEFAULT_REGION_CODE?: string;
	GOOGLE_MAPS_MAX_CANDIDATES?: number;
};

type MapsTestPayload = {
	place_name: string;
	location_context?: string;
	query_hint?: string;
	language_code?: string;
	region_code?: string;
	max_candidates?: number;
};

export const getMapsConfig = async (token: string) => {
	let error = null;

	const res = await fetch(`${MAPS_API_BASE_URL}/config`, {
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
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const updateMapsConfig = async (
	token: string,
	payload: { maps: MapsConfigPayload }
) => {
	let error = null;

	const res = await fetch(`${MAPS_API_BASE_URL}/config/update`, {
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
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const testMapsConfig = async (token: string, payload: MapsTestPayload) => {
	let error = null;

	const res = await fetch(`${MAPS_API_BASE_URL}/test`, {
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

import { NEWS_API_BASE_URL } from '$lib/constants';

const requestJson = async (token: string, path: string, options: RequestInit = {}) => {
	let error = null;

	const res = await fetch(`${NEWS_API_BASE_URL}${path}`, {
		...options,
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`,
			...(options.headers ?? {})
		}
	})
		.then(async (response) => {
			if (!response.ok) throw await response.json();
			return response.json();
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

export const getNewsConfig = async (token: string) => requestJson(token, '/config');

export const updateNewsConfig = async (token: string, payload: object) =>
	requestJson(token, '/config', {
		method: 'POST',
		body: JSON.stringify(payload)
	});

export const getNewsSourceRegistry = async (token: string) =>
	requestJson(token, '/source-registry');

export const updateNewsSourceRegistry = async (token: string, registry: object[]) =>
	requestJson(token, '/source-registry', {
		method: 'POST',
		body: JSON.stringify({ registry })
	});

export const getNewsCategories = async (token: string) => requestJson(token, '/categories');

export const getLatestNewsSnapshot = async (token: string) =>
	requestJson(token, '/latest-snapshot');

export const getLatestNewsBriefing = async (token: string) =>
	requestJson(token, '/latest-briefing');

export const getNewsThread = async (token: string, threadId: string) =>
	requestJson(token, `/threads/${encodeURIComponent(threadId)}`);

export const updateNewsCategories = async (token: string, categories: object[]) =>
	requestJson(token, '/categories', {
		method: 'POST',
		body: JSON.stringify({ categories })
	});

export const runNewsHourly = async (token: string) =>
	requestJson(token, '/worker/run-hourly', { method: 'POST' });

export const runNewsMorning = async (token: string) =>
	requestJson(token, '/worker/run-morning', { method: 'POST' });

export const runNewsDaily = async (token: string) =>
	requestJson(token, '/worker/run-daily', { method: 'POST' });

export const playLatestNews = async (token: string) =>
	requestJson(token, '/worker/play-latest', { method: 'POST' });

import { WEBUI_API_BASE_URL } from '$lib/constants';

export type RuntimeResolvedParams = {
	models_max: number;
	ctx: number;
	batch: number;
	ubatch_size: number;
	cache_prompt: boolean;
	cache_reuse: number;
	cache_k: string;
	cache_v: string;
	extra_args: string[];
};

export type LauncherStatus = {
	state_version: string;
	running: boolean;
	profile: string;
	pid: string;
	server_mode: string;
	model_path: string;
	models_dir: string;
	host: string;
	port: number;
	log_file: string;
	pid_file: string;
	model_file: string;
	resolved_params: RuntimeResolvedParams;
	error: string;
};

export type RuntimeCompatibility = {
	profile_compatibility: 'ok' | 'warning';
	issues: string[];
};

export type RuntimeStatus = {
	state: 'stopped' | 'starting' | 'running' | 'stopping' | 'error';
	running: boolean;
	profile: string;
	launcher_status: LauncherStatus;
	resolved_params: RuntimeResolvedParams;
	compatibility: RuntimeCompatibility;
	last_error?: string | null;
	script_path: string;
};

export type RuntimeLogs = {
	log_file: string;
	lines_requested: number;
	lines: string[];
	error?: string | null;
};

const runtimeFetch = async <T>(token: string, path: string, init: RequestInit = {}): Promise<T> => {
	let error = null;

	const response = await fetch(`${WEBUI_API_BASE_URL}/runtime${path}`, {
		...init,
		headers: {
			Accept: 'application/json',
			'Content-Type': 'application/json',
			authorization: `Bearer ${token}`,
			...(init.headers ?? {})
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			error = err.detail ?? `${err}`;
			console.error(err);
			return null;
		});

	if (error) {
		throw error;
	}

	return response as T;
};

export const getRuntimeStatus = async (token: string = '') => {
	return runtimeFetch<RuntimeStatus>(token, '/status');
};

export const getRuntimeLogs = async (token: string = '', lines: number = 200) => {
	return runtimeFetch<RuntimeLogs>(token, `/logs?lines=${lines}`);
};

const postRuntimeAction = async (
	token: string = '',
	path: '/start' | '/restart' | '/stop',
	body?: { profile: 'dual' | 'beast' }
) => {
	return runtimeFetch<RuntimeStatus>(token, path, {
		method: 'POST',
		body: body ? JSON.stringify(body) : undefined
	});
};

export const startRuntimeProfile = async (token: string = '', profile: 'dual' | 'beast') => {
	return postRuntimeAction(token, '/start', { profile });
};

export const restartRuntimeProfile = async (token: string = '', profile: 'dual' | 'beast') => {
	return postRuntimeAction(token, '/restart', { profile });
};

export const stopRuntime = async (token: string = '') => {
	return postRuntimeAction(token, '/stop');
};

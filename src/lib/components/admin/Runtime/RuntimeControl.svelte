<script lang="ts">
	import { browser } from '$app/environment';
	import { onDestroy, onMount, getContext } from 'svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import {
		getRuntimeLogs,
		getRuntimeStatus,
		restartRuntimeProfile,
		startRuntimeProfile,
		stopRuntime,
		type RuntimeLogs,
		type RuntimeStatus
	} from '$lib/apis/runtime';

	const i18n = getContext('i18n');

	let status: RuntimeStatus | null = null;
	let logs: RuntimeLogs | null = null;
	let loading = true;
	let actionLoading = false;
	let autoRefresh = true;
	let lastError = '';
	let mounted = false;
	let pollHandle: ReturnType<typeof setInterval> | null = null;
	const pollIntervalMs = 3000;
	const logLineCount = 160;

	const loadRuntime = async () => {
		try {
			lastError = '';
			const [nextStatus, nextLogs] = await Promise.all([
				getRuntimeStatus(localStorage.token),
				getRuntimeLogs(localStorage.token, logLineCount)
			]);
			status = nextStatus;
			logs = nextLogs;
		} catch (error) {
			console.error('Runtime status load failed:', error);
			lastError = `${error}`;
		} finally {
			loading = false;
		}
	};

	const refreshNow = async () => {
		loading = true;
		await loadRuntime();
	};

	const runAction = async (action: 'start' | 'restart' | 'stop', profile?: 'dual' | 'beast') => {
		actionLoading = true;
		try {
			lastError = '';
			if (action === 'start' && profile) {
				status = await startRuntimeProfile(localStorage.token, profile);
			} else if (action === 'restart' && profile) {
				status = await restartRuntimeProfile(localStorage.token, profile);
			} else {
				status = await stopRuntime(localStorage.token);
			}
			logs = await getRuntimeLogs(localStorage.token, logLineCount);
		} catch (error) {
			console.error(`Runtime ${action} failed:`, error);
			lastError = `${error}`;
		} finally {
			actionLoading = false;
		}
	};

	const syncPolling = (enabled: boolean) => {
		if (pollHandle) {
			clearInterval(pollHandle);
			pollHandle = null;
		}
		if (enabled) {
			pollHandle = setInterval(() => {
				loadRuntime();
			}, pollIntervalMs);
		}
	};

	const formatList = (values?: string[] | null) => {
		if (!values?.length) return '—';
		return values.join(' ');
	};

	const stateTone = (value?: RuntimeStatus['state']) => {
		if (value === 'running') return 'text-emerald-600 dark:text-emerald-400';
		if (value === 'error') return 'text-red-600 dark:text-red-400';
		if (value === 'starting' || value === 'stopping') return 'text-amber-600 dark:text-amber-400';
		return 'text-gray-700 dark:text-gray-200';
	};

	$: startDisabled = actionLoading || !status || status.state !== 'stopped';
	$: restartDisabled =
		actionLoading || !status || status.state === 'starting' || status.state === 'stopping';
	$: stopDisabled =
		actionLoading || !status || (status.state !== 'running' && status.state !== 'starting');
	$: compatibilityWarning =
		status?.compatibility?.profile_compatibility === 'warning' ? status.compatibility : null;

	$: if (browser && mounted) {
		syncPolling(autoRefresh);
	}

	onMount(async () => {
		mounted = true;
		await loadRuntime();
	});

	onDestroy(() => {
		mounted = false;
		if (pollHandle) {
			clearInterval(pollHandle);
		}
	});
</script>

<div class="flex flex-col gap-4">
	<div
		class="flex flex-col gap-3 rounded-xl border border-gray-100 bg-white p-4 dark:border-gray-800 dark:bg-gray-900"
	>
		<div class="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
			<div>
				<div class="text-lg font-medium text-gray-900 dark:text-white">{$i18n.t('Runtime')}</div>
				<div class="text-xs text-gray-500 dark:text-gray-400">
					{$i18n.t('Admin control surface for the local llama.cpp runtime profiles.')}
				</div>
			</div>
			<div class="flex flex-wrap items-center gap-2">
				<label
					class="flex items-center gap-2 rounded-md border border-gray-200 px-3 py-1.5 text-xs text-gray-600 dark:border-gray-700 dark:text-gray-300"
				>
					<input bind:checked={autoRefresh} type="checkbox" />
					<span>{$i18n.t('Auto refresh')}</span>
				</label>
				<button
					class="rounded-md border border-gray-200 px-3 py-1.5 text-xs font-medium text-gray-700 transition hover:bg-gray-50 dark:border-gray-700 dark:text-gray-200 dark:hover:bg-gray-800"
					disabled={loading || actionLoading}
					on:click={refreshNow}
				>
					{$i18n.t('Refresh')}
				</button>
			</div>
		</div>

		{#if compatibilityWarning}
			<div
				class="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-200"
			>
				<div class="font-medium">
					{$i18n.t('Current profile: {{profile}}', { profile: status?.profile ?? 'manual' })}
				</div>
				<div class="pt-1">
					{$i18n.t('OWUI specialist/task-model settings may not match this profile.')}
				</div>
				<ul class="list-disc pl-5 pt-2">
					{#each compatibilityWarning.issues as issue}
						<li>{issue}</li>
					{/each}
				</ul>
			</div>
		{/if}

		{#if lastError || status?.last_error}
			<div
				class="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800 dark:border-red-900/60 dark:bg-red-950/30 dark:text-red-200"
			>
				{lastError || status?.last_error}
			</div>
		{/if}

		{#if loading && !status}
			<div class="flex items-center gap-3 py-8 text-sm text-gray-500 dark:text-gray-400">
				<Spinner className="size-4" />
				<span>{$i18n.t('Loading runtime status...')}</span>
			</div>
		{:else if status}
			<div class="grid gap-3 md:grid-cols-2 xl:grid-cols-6">
				<div class="rounded-lg bg-gray-50 px-3 py-3 dark:bg-gray-800/70">
					<div class="text-[11px] uppercase tracking-wide text-gray-500 dark:text-gray-400">
						{$i18n.t('State')}
					</div>
					<div class={`pt-1 text-sm font-medium ${stateTone(status.state)}`}>{status.state}</div>
				</div>
				<div class="rounded-lg bg-gray-50 px-3 py-3 dark:bg-gray-800/70">
					<div class="text-[11px] uppercase tracking-wide text-gray-500 dark:text-gray-400">
						{$i18n.t('Profile')}
					</div>
					<div class="pt-1 text-sm font-medium text-gray-900 dark:text-white">
						{status.profile || 'manual'}
					</div>
				</div>
				<div class="rounded-lg bg-gray-50 px-3 py-3 dark:bg-gray-800/70">
					<div class="text-[11px] uppercase tracking-wide text-gray-500 dark:text-gray-400">
						{$i18n.t('PID')}
					</div>
					<div class="pt-1 text-sm font-medium text-gray-900 dark:text-white">
						{status.launcher_status.pid || '—'}
					</div>
				</div>
				<div class="rounded-lg bg-gray-50 px-3 py-3 dark:bg-gray-800/70">
					<div class="text-[11px] uppercase tracking-wide text-gray-500 dark:text-gray-400">
						{$i18n.t('Port')}
					</div>
					<div class="pt-1 text-sm font-medium text-gray-900 dark:text-white">
						{status.launcher_status.port || '—'}
					</div>
				</div>
				<div class="rounded-lg bg-gray-50 px-3 py-3 dark:bg-gray-800/70">
					<div class="text-[11px] uppercase tracking-wide text-gray-500 dark:text-gray-400">
						{$i18n.t('Models Max')}
					</div>
					<div class="pt-1 text-sm font-medium text-gray-900 dark:text-white">
						{status.resolved_params.models_max}
					</div>
				</div>
				<div class="rounded-lg bg-gray-50 px-3 py-3 dark:bg-gray-800/70">
					<div class="text-[11px] uppercase tracking-wide text-gray-500 dark:text-gray-400">
						{$i18n.t('Context')}
					</div>
					<div class="pt-1 text-sm font-medium text-gray-900 dark:text-white">
						{status.resolved_params.ctx}
					</div>
				</div>
			</div>

			<div class="grid gap-3 lg:grid-cols-[minmax(0,1fr),auto]">
				<div
					class="rounded-lg bg-gray-50 px-3 py-3 text-xs text-gray-600 dark:bg-gray-800/70 dark:text-gray-300"
				>
					<div class="font-medium text-gray-900 dark:text-white">{$i18n.t('Script Path')}</div>
					<div class="pt-1 break-all">{status.script_path}</div>
					<div class="pt-3 font-medium text-gray-900 dark:text-white">{$i18n.t('Extra Args')}</div>
					<div class="pt-1 break-words">{formatList(status.resolved_params.extra_args)}</div>
				</div>

				<div class="flex flex-wrap gap-2 self-start">
					<button
						class="rounded-md bg-gray-900 px-3 py-1.5 text-xs font-medium text-white transition hover:bg-black disabled:opacity-60 dark:bg-white dark:text-gray-900 dark:hover:bg-gray-100"
						disabled={startDisabled}
						on:click={() => runAction('start', 'dual')}
					>
						{$i18n.t('Start Dual')}
					</button>
					<button
						class="rounded-md border border-gray-200 px-3 py-1.5 text-xs font-medium text-gray-700 transition hover:bg-gray-50 disabled:opacity-60 dark:border-gray-700 dark:text-gray-200 dark:hover:bg-gray-800"
						disabled={restartDisabled}
						on:click={() => runAction('restart', 'dual')}
					>
						{$i18n.t('Restart Dual')}
					</button>
					<button
						class="rounded-md bg-gray-900 px-3 py-1.5 text-xs font-medium text-white transition hover:bg-black disabled:opacity-60 dark:bg-white dark:text-gray-900 dark:hover:bg-gray-100"
						disabled={startDisabled}
						on:click={() => runAction('start', 'beast')}
					>
						{$i18n.t('Start Beast')}
					</button>
					<button
						class="rounded-md border border-gray-200 px-3 py-1.5 text-xs font-medium text-gray-700 transition hover:bg-gray-50 disabled:opacity-60 dark:border-gray-700 dark:text-gray-200 dark:hover:bg-gray-800"
						disabled={restartDisabled}
						on:click={() => runAction('restart', 'beast')}
					>
						{$i18n.t('Restart Beast')}
					</button>
					<button
						class="rounded-md border border-red-200 px-3 py-1.5 text-xs font-medium text-red-700 transition hover:bg-red-50 disabled:opacity-60 dark:border-red-900/60 dark:text-red-300 dark:hover:bg-red-950/40"
						disabled={stopDisabled}
						on:click={() => runAction('stop')}
					>
						{$i18n.t('Stop')}
					</button>
				</div>
			</div>
		{/if}
	</div>

	<div
		class="flex flex-col gap-3 rounded-xl border border-gray-100 bg-white p-4 dark:border-gray-800 dark:bg-gray-900"
	>
		<div class="flex items-center justify-between">
			<div>
				<div class="text-base font-medium text-gray-900 dark:text-white">
					{$i18n.t('Recent Logs')}
				</div>
				<div class="text-xs text-gray-500 dark:text-gray-400">
					{$i18n.t('Bounded recent lines from the launcher log file.')}
				</div>
			</div>
			<div class="text-xs text-gray-500 dark:text-gray-400">
				{logs?.log_file || status?.launcher_status.log_file || '—'}
			</div>
		</div>

		{#if logs?.error}
			<div
				class="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800 dark:border-red-900/60 dark:bg-red-950/30 dark:text-red-200"
			>
				{logs.error}
			</div>
		{:else}
			<pre
				class="max-h-[28rem] overflow-auto rounded-lg bg-gray-950 px-4 py-3 text-xs text-gray-100">{logs?.lines?.join(
					'\n'
				) || ''}</pre>
		{/if}
	</div>
</div>

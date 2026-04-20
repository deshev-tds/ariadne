<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { onMount, getContext } from 'svelte';
	import { v4 as uuidv4 } from 'uuid';
	import {
		getScholarlyConfig,
		testScholarlySource,
		updateScholarlyConfig,
		type ScholarlySourcePayload,
		type ScholarlyTestResponse
	} from '$lib/apis/scholarly';

	const i18n = getContext('i18n');

	import { terminalServers } from '$lib/stores';
	import { getTerminalServers } from '$lib/apis/terminal';
	import { WEBUI_API_BASE_URL } from '$lib/constants';

	import Switch from '$lib/components/common/Switch.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import SensitiveInput from '$lib/components/common/SensitiveInput.svelte';
	import Plus from '$lib/components/icons/Plus.svelte';
	import Cog6 from '$lib/components/icons/Cog6.svelte';
	import Cloud from '$lib/components/icons/Cloud.svelte';
	import Connection from '$lib/components/chat/Settings/Tools/Connection.svelte';

	import AddToolServerModal from '$lib/components/AddToolServerModal.svelte';
	import AddTerminalServerModal from '$lib/components/AddTerminalServerModal.svelte';
	import ConfirmDialog from '$lib/components/common/ConfirmDialog.svelte';

	import {
		getToolServerConnections,
		setToolServerConnections,
		getTerminalServerConnections,
		setTerminalServerConnections
	} from '$lib/apis/configs';

	let servers = null;
	let showConnectionModal = false;

	// Terminal server admin connections
	let terminalConnections = [];
	let showAddTerminalModal = false;
	let editTerminalIdx: number | null = null;
	let showDeleteTerminalConfirm = false;
	let deleteTerminalIdx: number | null = null;
	let scholarlySources: ScholarlySourcePayload[] = [];
	let scholarlySourcesBusy = false;
	let scholarlySourcesSaveBusy = false;
	let scholarlyTestBusy: Record<string, boolean> = {};
	let scholarlyTestResults: Record<string, ScholarlyTestResponse | string | null> = {};

	const getPrettyJson = (value) => {
		if (value == null) {
			return '';
		}
		if (typeof value === 'string') {
			return value;
		}
		return JSON.stringify(value, null, 2);
	};

	const getScholarlyProtocol = (
		value: ScholarlyTestResponse | string | null
	): ScholarlyTestResponse['protocol'] | null => {
		if (!value || typeof value === 'string' || !('protocol' in value)) {
			return null;
		}
		return value.protocol ?? null;
	};

	const getScholarlyProtocolStatus = (
		value: ScholarlyTestResponse | string | null
	): 'pass' | 'warn' | 'fail' | null => getScholarlyProtocol(value)?.status ?? null;

	const getScholarlyProtocolSummary = (value: ScholarlyTestResponse | string | null): string =>
		getScholarlyProtocol(value)?.summary ?? '';

	const getScholarlyProtocolBadgeClass = (status: 'pass' | 'warn' | 'fail') => {
		if (status === 'pass') {
			return 'bg-emerald-100 text-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-300';
		}
		if (status === 'fail') {
			return 'bg-red-100 text-red-700 dark:bg-red-950/50 dark:text-red-300';
		}
		return 'bg-amber-100 text-amber-700 dark:bg-amber-950/50 dark:text-amber-300';
	};

	const addConnectionHandler = async (server) => {
		servers = [...servers, server];
		await updateHandler();
	};

	const updateHandler = async ({ showToast = true } = {}) => {
		const res = await setToolServerConnections(localStorage.token, {
			TOOL_SERVER_CONNECTIONS: servers
		}).catch(() => {
			toast.error($i18n.t('Failed to save connections'));
			return null;
		});

		if (res && showToast) {
			toast.success($i18n.t('Connections saved successfully'));
		}

		return Boolean(res);
	};

	const saveTerminalServers = async () => {
		const res = await setTerminalServerConnections(localStorage.token, {
			TERMINAL_SERVER_CONNECTIONS: terminalConnections
		}).catch(() => {
			toast.error($i18n.t('Failed to save terminal servers'));
			return null;
		});

		if (res) {
			toast.success($i18n.t('Terminal servers saved'));

			// Refresh the terminalServers store so changes are reflected immediately
			// Preserve user direct terminals, refresh system terminals from backend
			const existingDirectTerminals = ($terminalServers ?? []).filter((t) => !t.id);
			const systemTerminals = await getTerminalServers(localStorage.token);
			const systemEntries = systemTerminals.map((t) => ({
				id: t.id,
				url: `${WEBUI_API_BASE_URL}/terminals/${t.id}`,
				name: t.name,
				key: localStorage.token
			}));
			terminalServers.set([...existingDirectTerminals, ...systemEntries]);
		}
	};

	const addTerminalConnection = (server) => {
		terminalConnections = [...terminalConnections, { ...server, id: server.id ?? uuidv4() }];
		saveTerminalServers();
	};

	const updateTerminalConnection = (idx: number, updated) => {
		terminalConnections = terminalConnections.map((c, i) =>
			i === idx ? { ...c, ...updated, id: updated.id ?? c.id } : c
		);
		saveTerminalServers();
	};

	const removeTerminalConnection = (idx: number) => {
		terminalConnections = terminalConnections.filter((_, i) => i !== idx);
		saveTerminalServers();
	};

	const patchScholarlySource = (
		sourceId: string,
		patch: Partial<ScholarlySourcePayload['settings']>
	) => {
		scholarlySources = scholarlySources.map((source) =>
			source.id === sourceId
				? {
						...source,
						settings: {
							...source.settings,
							...patch
						}
					}
				: source
		);
	};

	const loadScholarlySources = async () => {
		scholarlySourcesBusy = true;
		try {
			const res = await getScholarlyConfig(localStorage.token);
			scholarlySources = res?.scholarly?.sources ?? [];
		} catch {
			scholarlySources = [];
			toast.error($i18n.t('Failed to load scholarly source settings'));
		} finally {
			scholarlySourcesBusy = false;
		}
	};

	const saveScholarlySources = async ({ showToast = true } = {}) => {
		scholarlySourcesSaveBusy = true;
		try {
			const payload = Object.fromEntries(
				scholarlySources.map((source) => [
					source.id,
					{
						enabled: source.settings.enabled,
						api_key: source.settings.api_key ?? ''
					}
				])
			);
			const res = await updateScholarlyConfig(localStorage.token, {
				scholarly: { sources: payload }
			});
			scholarlySources = res?.scholarly?.sources ?? scholarlySources;
			if (showToast) {
				toast.success($i18n.t('Scholarly API settings saved'));
			}
			return true;
		} catch (e) {
			toast.error(`${$i18n.t('Failed to save scholarly source settings')}: ${e}`);
			return false;
		} finally {
			scholarlySourcesSaveBusy = false;
		}
	};

	const runScholarlyTest = async (source: ScholarlySourcePayload) => {
		scholarlyTestBusy = { ...scholarlyTestBusy, [source.id]: true };
		scholarlyTestResults = { ...scholarlyTestResults, [source.id]: null };

		try {
			const res = await testScholarlySource(localStorage.token, {
				source_id: source.id,
				settings_override: {
					enabled: source.settings.enabled,
					api_key: source.settings.api_key ?? ''
				}
			});
			scholarlyTestResults = { ...scholarlyTestResults, [source.id]: res };
		} catch (e) {
			scholarlyTestResults = {
				...scholarlyTestResults,
				[source.id]: typeof e === 'string' ? e : JSON.stringify(e, null, 2)
			};
		} finally {
			scholarlyTestBusy = { ...scholarlyTestBusy, [source.id]: false };
		}
	};

	onMount(async () => {
		const res = await getToolServerConnections(localStorage.token);
		servers = res.TOOL_SERVER_CONNECTIONS;

		await Promise.all([
			(async () => {
				try {
					const terminalRes = await getTerminalServerConnections(localStorage.token);
					if (terminalRes?.TERMINAL_SERVER_CONNECTIONS) {
						terminalConnections = terminalRes.TERMINAL_SERVER_CONNECTIONS;
					}
				} catch {
					// Not configured yet
				}
			})(),
			loadScholarlySources()
		]);
	});
</script>

<AddToolServerModal bind:show={showConnectionModal} onSubmit={addConnectionHandler} />

<AddTerminalServerModal
	bind:show={showAddTerminalModal}
	edit={editTerminalIdx !== null}
	connection={editTerminalIdx !== null ? terminalConnections[editTerminalIdx] : null}
	onSubmit={(c) => {
		if (editTerminalIdx !== null) {
			updateTerminalConnection(editTerminalIdx, c);
			editTerminalIdx = null;
		} else {
			addTerminalConnection(c);
		}
	}}
	onDelete={() => {
		if (editTerminalIdx !== null) {
			deleteTerminalIdx = editTerminalIdx;
			showDeleteTerminalConfirm = true;
			editTerminalIdx = null;
		}
	}}
/>

<ConfirmDialog
	bind:show={showDeleteTerminalConfirm}
	on:confirm={() => {
		if (deleteTerminalIdx !== null) {
			removeTerminalConnection(deleteTerminalIdx);
			deleteTerminalIdx = null;
		}
	}}
/>

<form
	class="flex flex-col h-full justify-between text-sm"
	on:submit|preventDefault={async () => {
		const [toolServersSaved, scholarlySaved] = await Promise.all([
			updateHandler({ showToast: false }),
			saveScholarlySources({ showToast: false })
		]);
		if (toolServersSaved || scholarlySaved) {
			toast.success($i18n.t('Settings saved successfully!'));
		}
	}}
>
	<div class=" overflow-y-scroll scrollbar-hidden h-full">
		{#if servers !== null}
			<div class="">
				<div class="mb-3">
					<div class=" mt-0.5 mb-2.5 text-base font-medium">{$i18n.t('General')}</div>

					<hr class=" border-gray-100/30 dark:border-gray-850/30 my-2" />

					<div class="mb-2.5 flex flex-col w-full justify-between">
						<div class="flex justify-between items-center mb-0.5">
							<div class="font-medium">{$i18n.t('Manage Tool Servers')}</div>

							<Tooltip content={$i18n.t(`Add Connection`)}>
								<button
									class="px-1"
									on:click={() => {
										showConnectionModal = true;
									}}
									type="button"
								>
									<Plus />
								</button>
							</Tooltip>
						</div>

						<div class="flex flex-col gap-1">
							{#each servers as server, idx}
								<Connection
									bind:connection={server}
									onSubmit={() => {
										updateHandler();
									}}
									onDelete={() => {
										servers = servers.filter((_, i) => i !== idx);
										updateHandler();
									}}
								/>
							{/each}
						</div>

						{#if servers.length === 0}
							<div class="text-xs text-gray-400 dark:text-gray-500">
								{$i18n.t('No tool server connections configured.')}
							</div>
						{/if}

						<div class="my-1.5">
							<div class="text-xs text-gray-500">
								{$i18n.t('Connect to your own OpenAPI compatible external tool servers.')}
							</div>
						</div>
					</div>

					<hr class=" border-gray-100/30 dark:border-gray-850/30 my-4" />

					<div class="mb-2.5 flex flex-col w-full">
						<div class="flex justify-between items-center mb-1">
							<div class="flex items-center gap-2">
								<div class="font-medium">{$i18n.t('Open Terminal')}</div>
								<span
									class="text-[0.65rem] font-medium uppercase px-1.5 py-0.5 rounded-full bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400"
									>{$i18n.t('Experimental')}</span
								>
							</div>

							<Tooltip content={$i18n.t('Add Connection')}>
								<button
									class="px-1"
									on:click={() => {
										editTerminalIdx = null;
										showAddTerminalModal = true;
									}}
									type="button"
								>
									<Plus />
								</button>
							</Tooltip>
						</div>

						<div class="flex flex-col gap-1.5">
							{#each terminalConnections as connection, idx}
								<div class="flex w-full gap-2 items-center">
									<Tooltip className="w-full relative" content={''} placement="top-start">
										<div class="flex w-full">
											<div
												class="flex-1 relative flex gap-1.5 items-center {connection?.enabled ===
												false
													? 'opacity-50'
													: ''}"
											>
												<Tooltip content={$i18n.t('Terminal')}>
													<Cloud className="size-4" strokeWidth="1.5" />
												</Tooltip>

												<div class="outline-hidden w-full bg-transparent text-sm">
													{connection.name || connection.url || $i18n.t('New Terminal')}
												</div>
											</div>
										</div>
									</Tooltip>

									<div class="flex gap-1 items-center">
										<Tooltip content={$i18n.t('Configure')}>
											<button
												class="self-center p-1 bg-transparent hover:bg-gray-100 dark:hover:bg-gray-850 rounded-lg transition"
												on:click={() => {
													editTerminalIdx = idx;
													showAddTerminalModal = true;
												}}
												type="button"
											>
												<Cog6 />
											</button>
										</Tooltip>

										<Tooltip
											content={connection?.enabled !== false
												? $i18n.t('Enabled')
												: $i18n.t('Disabled')}
										>
											<Switch
												state={connection?.enabled !== false}
												on:change={() => {
													terminalConnections = terminalConnections.map((c, i) =>
														i === idx ? { ...c, enabled: !(c?.enabled !== false) } : c
													);
													saveTerminalServers();
												}}
											/>
										</Tooltip>
									</div>
								</div>
							{/each}
						</div>

						{#if terminalConnections.length === 0}
							<div class="text-xs text-gray-400 dark:text-gray-500">
								{$i18n.t('No terminal connections configured.')}
							</div>
						{/if}

						<div class="mt-1.5">
							<div class="text-xs text-gray-500">
								{$i18n.t(
									'Connect to Open Terminal instances. All users will have access to file browsing and terminal tools through these servers.'
								)}
							</div>
							<div class="text-xs text-gray-600 dark:text-gray-300 mt-1">
								<a
									class="underline"
									href="https://github.com/open-webui/open-terminal"
									target="_blank">{$i18n.t('Learn more about Open Terminal')} ↗</a
								>
							</div>
						</div>
					</div>

					<hr class=" border-gray-100/30 dark:border-gray-850/30 my-4" />

					<div class="mb-2.5 flex flex-col w-full">
						<div class="flex justify-between items-center mb-1">
							<div class="flex items-center gap-2">
								<div class="font-medium">{$i18n.t('Scholarly APIs')}</div>
								<span
									class="text-[0.65rem] font-medium uppercase px-1.5 py-0.5 rounded-full bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400"
									>{$i18n.t('Planned')}</span
								>
							</div>
							{#if scholarlySourcesBusy || scholarlySourcesSaveBusy}
								<Spinner className="size-4" />
							{/if}
						</div>

						<div class="text-xs text-gray-500">
							{$i18n.t(
								'API-first academic sources that Science lane should target natively. Each source can be enabled independently and tested without hiding the upstream response.'
							)}
						</div>
						<div class="text-xs text-gray-500 mt-1">
							{$i18n.t(
								'Planner fallback coverage is derived from the Web Search source registry, while direct API integrations remain the next implementation step.'
							)}
						</div>

						<div
							class="mt-3 rounded-xl border border-gray-200 dark:border-gray-800 overflow-hidden"
						>
							{#each scholarlySources as source, idx}
								<div
									class="px-3 py-3 {idx > 0
										? 'border-t border-gray-100 dark:border-gray-850/60'
										: ''}"
								>
									<div class="flex items-start justify-between gap-3">
										<div class="min-w-0">
											<div class="font-medium">{source.label}</div>
											<div class="text-xs text-gray-500 mt-1">{source.purpose}</div>
											<div class="text-xs text-gray-500 mt-1">
												{$i18n.t('Planner fallback domains')}:
												{source.planner_fallback_domains.join(', ')}
											</div>
										</div>
										<div class="flex items-center gap-2 shrink-0">
											<Tooltip
												content={source.settings.enabled ? $i18n.t('Enabled') : $i18n.t('Disabled')}
											>
												<Switch
													state={source.settings.enabled}
													on:change={() => {
														patchScholarlySource(source.id, {
															enabled: !source.settings.enabled
														});
													}}
												/>
											</Tooltip>
											<button
												class="px-3 py-1.5 text-xs font-medium bg-gray-100 hover:bg-gray-200 text-gray-900 dark:bg-gray-800 dark:text-gray-100 dark:hover:bg-gray-700 transition rounded-full disabled:opacity-70"
												type="button"
												disabled={scholarlyTestBusy[source.id]}
												on:click={() => runScholarlyTest(source)}
											>
												{#if scholarlyTestBusy[source.id]}
													{$i18n.t('Testing...')}
												{:else}
													{$i18n.t('Test')}
												{/if}
											</button>
										</div>
									</div>

									<div class="mt-3 grid grid-cols-1 xl:grid-cols-[1.15fr_1fr_1fr] gap-3">
										<div>
											<div class="text-xs font-medium mb-1">{$i18n.t('Auth')}</div>
											<div class="text-sm text-gray-700 dark:text-gray-300">
												{#if source.auth_mode === 'required'}
													{$i18n.t('API key required')}
												{:else if source.auth_mode === 'optional'}
													{$i18n.t('API key optional')}
												{:else}
													{$i18n.t('No API key')}
												{/if}
											</div>
											<div class="text-xs text-gray-500 mt-1">{source.auth_detail}</div>
										</div>

										<div>
											<div class="text-xs font-medium mb-1">{$i18n.t('Status')}</div>
											<div class="text-sm">{source.ariadne_status}</div>
											<div
												class="text-xs mt-1 {source.admin_probe_ready
													? 'text-emerald-600 dark:text-emerald-400'
													: 'text-gray-500'}"
											>
												{$i18n.t('Admin probe')}:
												{source.admin_probe_ready ? $i18n.t('Ready') : $i18n.t('Missing')}
											</div>
											<div
												class="text-xs mt-1 {source.planner_fallback_configured
													? 'text-emerald-600 dark:text-emerald-400'
													: 'text-gray-500'}"
											>
												{$i18n.t('Planner fallback')}:
												{source.planner_fallback_configured
													? $i18n.t('Configured')
													: $i18n.t('Pending')}
												{#if source.covered_domains.length > 0}
													<span class="text-gray-500 dark:text-gray-400">
														({source.covered_domains.join(', ')})
													</span>
												{/if}
											</div>
											<div class="text-xs mt-1 text-gray-500">
												{$i18n.t('Native tool adapter')}: {source.native_tool_adapter_status}
											</div>
											<div class="text-xs mt-1 text-gray-500">
												{$i18n.t('Skill support')}: {source.skill_support_status}
											</div>
											{#if source.seeded_skill_ids.length > 0}
												<div class="text-xs mt-1 text-gray-500">
													{$i18n.t('Code-backed skills')}:
													{source.seeded_skill_ids.join(', ')}
												</div>
											{/if}
										</div>

										<div>
											<div class="text-xs font-medium mb-1">{$i18n.t('Notes')}</div>
											<div class="text-xs text-gray-500 space-y-1">
												{#each source.notes as note}
													<div>{note}</div>
												{/each}
												{#if source.uses_contact_email}
													<div>
														{$i18n.t('Contact email')}:
														{source.effective_contact_email || $i18n.t('Will be captured on save')}
													</div>
												{/if}
												<div>{source.inventory_scope_note}</div>
											</div>
										</div>
									</div>

									{#if source.api_key_label}
										<div class="mt-3">
											<div class="text-xs font-medium mb-1">{source.api_key_label}</div>
											<div
												class="rounded-lg px-4 py-2 bg-gray-50 dark:bg-gray-850 flex items-center"
											>
												<SensitiveInput
													placeholder={source.api_key_placeholder ?? ''}
													bind:value={source.settings.api_key}
													outerClassName="flex flex-1 bg-transparent"
													inputClassName="w-full rounded-lg text-sm bg-transparent outline-hidden"
													showButtonClassName="pl-2 transition bg-transparent"
													screenReader={false}
													required={false}
												/>
											</div>
										</div>
									{/if}

									{#if scholarlyTestResults[source.id]}
										<div class="mt-3">
											<div class="text-xs font-medium mb-1">{$i18n.t('Test Result')}</div>
											{#if getScholarlyProtocol(scholarlyTestResults[source.id])}
												<div
													class="mb-2 rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs dark:border-gray-800 dark:bg-gray-900"
												>
													<div class="flex items-center gap-2">
														<span
															class="rounded-full px-2 py-0.5 font-medium uppercase {getScholarlyProtocolBadgeClass(
																getScholarlyProtocolStatus(scholarlyTestResults[source.id]) ??
																	'warn'
															)}"
														>
															{getScholarlyProtocolStatus(scholarlyTestResults[source.id])}
														</span>
														<span class="text-gray-600 dark:text-gray-300">
															{getScholarlyProtocolSummary(scholarlyTestResults[source.id])}
														</span>
													</div>
												</div>
											{/if}
											<div
												class="w-full rounded-lg py-3 px-4 text-xs bg-gray-50 dark:text-gray-300 dark:bg-gray-850 overflow-x-auto whitespace-pre-wrap break-words font-mono"
											>
												{getPrettyJson(scholarlyTestResults[source.id])}
											</div>
										</div>
									{/if}
								</div>
							{/each}
						</div>
					</div>
				</div>
			</div>
		{:else}
			<div class="flex h-full justify-center">
				<div class="my-auto">
					<Spinner className="size-6" />
				</div>
			</div>
		{/if}
	</div>

	<div class="flex justify-end pt-3 text-sm font-medium">
		<button
			class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full"
			type="submit"
		>
			{$i18n.t('Save')}
		</button>
	</div>
</form>

<script lang="ts">
	import { v4 as uuidv4 } from 'uuid';
	import { toast } from 'svelte-sonner';
	import { PaneGroup, Pane, PaneResizer } from 'paneforge';

	import { getContext, onDestroy, onMount, tick } from 'svelte';
	import { fade } from 'svelte/transition';
	const i18n: Writable<i18nType> = getContext('i18n');

	import { beforeNavigate, goto } from '$app/navigation';
	import { page } from '$app/stores';

	import { get, type Unsubscriber, type Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import { WEBUI_BASE_URL } from '$lib/constants';

	import {
		chatId,
		chats,
		config,
		type Model,
		models,
		personas,
		tags as allTags,
		settings,
		showSidebar,
		WEBUI_NAME,
		banners,
		user,
		socket,
		audioQueue,
		showControls,
		showCallOverlay,
		currentChatPage,
		temporaryChatEnabled,
		mobile,
		chatTitle,
		showArtifacts,
		artifactContents,
		tools,
		toolServers,
		terminalServers,
		functions,
		selectedFolder,
		pinnedChats,
		showEmbeds,
		selectedTerminalId,
		showFileNavPath,
		showFileNavDir
	} from '$lib/stores';

	import { WEBUI_API_BASE_URL } from '$lib/constants';

	import {
		convertMessagesToHistory,
		copyToClipboard,
		getMessageContentParts,
		createMessagesList,
		getPromptVariables,
		processDetails,
		removeAllDetails,
		getCodeBlockContents,
		isYoutubeUrl,
		displayFileHandler
	} from '$lib/utils';
	import {
		normalizeHistoryModelSelections,
		normalizeModelSelection
	} from '$lib/utils/model-selection';
	import { AudioQueue } from '$lib/utils/audio';

	import {
		archiveChatById,
		createNewChat,
		getAllTags,
		getChatById,
		getChatList,
		getContextWindowPreview,
		getPinnedChatList,
		getTagsById,
		updateChatById,
		updateChatFolderIdById
	} from '$lib/apis/chats';
	import type { ContextWindowPreview } from '$lib/apis/chats';
	import { generateOpenAIChatCompletion } from '$lib/apis/openai';
	import { processWeb, processWebSearch, processYoutubeVideo } from '$lib/apis/retrieval';
	import { getAndUpdateUserLocation, getUserSettings } from '$lib/apis/users';
	import {
		chatCompleted,
		generateQueries,
		chatAction,
		generateMoACompletion,
		stopTask,
		getTaskIdsByChatId
	} from '$lib/apis';
	import { getTools } from '$lib/apis/tools';
	import { uploadFile } from '$lib/apis/files';
	import { createOpenAITextStream } from '$lib/apis/streaming';
	import { getFunctions } from '$lib/apis/functions';
	import { updateFolderById } from '$lib/apis/folders';

	import Banner from '../common/Banner.svelte';
	import MessageInput from '$lib/components/chat/MessageInput.svelte';
	import Messages from '$lib/components/chat/Messages.svelte';
	import Navbar from '$lib/components/chat/Navbar.svelte';
	import SceneNoteModal from '$lib/components/chat/SceneNoteModal.svelte';
	import ChatControls from './ChatControls.svelte';
	import EventConfirmDialog from '../common/ConfirmDialog.svelte';
	import Placeholder from './Placeholder.svelte';
	import FilesOverlay from './MessageInput/FilesOverlay.svelte';
	import NotificationToast from '../NotificationToast.svelte';
	import Spinner from '../common/Spinner.svelte';
	import Tooltip from '../common/Tooltip.svelte';
	import Sidebar from '../icons/Sidebar.svelte';
	import Image from '../common/Image.svelte';
	import {
		applyCompletionTokenData,
		applyTokenExplorerDefaults,
		buildTokenBranchDisplayPrefix,
		buildTokenBranchPayload,
		type TokenBranchRequest
	} from './tokenExplorer';
	import {
		normalizeScienceAttachedCorpora,
		normalizeScienceResearchMode,
		resolveScienceLaneTerminalId,
		type ScienceResearchMode
	} from './scienceLane';
	import { getBanners } from '$lib/apis/configs';
	import type { Persona } from '$lib/apis/personas';
	import {
		buildPersonaChatMeta,
		buildPersonaDefaultsSnapshot,
		getActiveChatIdentity,
		getEffectiveModelBinding,
		getEffectivePersonaState,
		getEffectiveVoicePreference,
		getRequestedFeatureIdsFromFeatures
	} from '$lib/utils/personas';
	import { getSceneNoteLabel, normalizeSceneNote, type SceneNote } from '$lib/utils/sceneNotes';

	type RuntimeAwareModel = Model & {
		status?: {
			value?: string;
		};
	};

	export let chatIdProp = '';

	let loading = true;

	const eventTarget = new EventTarget();
	let controlPane: Pane | undefined;
	let controlPaneComponent: ChatControls | undefined;

	let messageInput: MessageInput | undefined;

	let autoScroll = true;
	let processing = '';
	let messagesContainerElement: HTMLDivElement;

	let navbarElement;

	let showEventConfirmation = false;
	let eventConfirmationTitle = '';
	let eventConfirmationMessage = '';
	let eventConfirmationInput = false;
	let eventConfirmationInputPlaceholder = '';
	let eventConfirmationInputValue = '';
	let eventConfirmationInputType = '';
	let eventCallback = null;

	let selectedModels = [''];
	let directSelectedModels = [''];
	let selectedPersonaId: string | null = null;
	let selectedPersona: Persona | null = null;
	let sceneNote: SceneNote | null = null;
	let showSceneNoteModal = false;
	let activeBoundModelId: string | null = null;
	let activeChatIdentity = null;
	let activeVoicePreference = { voiceId: null, speed: null };
	let activeSceneNoteLabel: string | null = null;
	let atSelectedModel: Model | undefined;
	let selectedModelIds = [];
	$: selectedPersona =
		selectedPersonaId && selectedPersonaId !== ''
			? (($personas ?? []).find((persona) => persona.id === selectedPersonaId) ?? null)
			: null;

	const hasOwn = (value: Record<string, any> | null | undefined, key: string) =>
		typeof value === 'object' && value !== null && Object.prototype.hasOwnProperty.call(value, key);

	const getSelectedPersonaChatMeta = () => {
		if (!selectedPersona || !chat || chat.persona_id !== selectedPersona.id) {
			return null;
		}

		return chat.meta ?? null;
	};

	const buildPersonaOverlayModel = (persona: Persona | null, model: Model | undefined) => {
		if (!persona || !model) {
			return model;
		}

		const personaState = getEffectivePersonaState({
			persona,
			chatMeta: getSelectedPersonaChatMeta(),
			model,
			tools: $tools ?? [],
			functions: $functions ?? [],
			config: $config,
			user: $user
		});

		const effective = personaState?.effective ?? null;
		if (!effective) {
			return model;
		}

		return {
			...model,
			name: persona.name,
			info: {
				...(model.info ?? {}),
				meta: {
					...(model.info?.meta ?? {}),
					profile_image_url: persona.profile_image_url ?? model.info?.meta?.profile_image_url,
					description: persona.description ?? model.info?.meta?.description,
					toolIds: effective.tool_ids,
					filterIds: effective.filter_ids,
					defaultFilterIds: effective.filter_ids,
					actionIds: effective.action_ids,
					defaultFeatureIds: effective.default_feature_ids,
					capabilities: {
						...(model.info?.meta?.capabilities ?? {}),
						...(effective.capabilities ?? {})
					},
					tts: {
						...(model.info?.meta?.tts ?? {}),
						...(effective.voice_id ? { voice: effective.voice_id } : {})
					}
				},
				params: {
					...(model.info?.params ?? {}),
					...(effective.system_prompt !== null && effective.system_prompt !== undefined
						? { system: effective.system_prompt }
						: {})
				}
			}
		};
	};

	$: activeBoundModelId = getEffectiveModelBinding({
		selectedPersona,
		selectedModels,
		chatMeta: getSelectedPersonaChatMeta()
	});

	$: activeChatIdentity = getActiveChatIdentity({
		persona: selectedPersona,
		model:
			atSelectedModel ??
			$models.find((model) => model.id === (activeBoundModelId ?? selectedModels[0]))
	});

	$: activeVoicePreference = getEffectiveVoicePreference({
		persona: selectedPersona,
		chatMeta: getSelectedPersonaChatMeta(),
		model:
			atSelectedModel ??
			$models.find((model) => model.id === (activeBoundModelId ?? selectedModels[0])),
		settings: $settings,
		config: $config
	});

	$: activeSceneNoteLabel = getSceneNoteLabel(sceneNote);

	$: {
		if (selectedPersona) {
			const boundModel = $models.find((model) => model.id === activeBoundModelId);
			atSelectedModel = buildPersonaOverlayModel(selectedPersona, boundModel);
		} else if (atSelectedModel && atSelectedModel.id !== selectedModels[0]) {
			atSelectedModel = undefined;
		}
	}

	$: if (!selectedPersona && selectedModels.filter((modelId) => modelId).length > 0) {
		directSelectedModels = [...selectedModels];
	}

	$: if (atSelectedModel !== undefined) {
		selectedModelIds = [atSelectedModel.id];
	} else {
		selectedModelIds = selectedModels;
	}

	let selectedToolIds = [];
	let selectedFilterIds = [];

	let imageGenerationEnabled = false;
	let webSearchEnabled = false;
	let codeInterpreterEnabled = false;

	let showCommands = false;

	let generating = false;
	let dragged = false;
	let generationController = null;
	let contextWindowPreview: ContextWindowPreview | null = null;
	let contextWindowRuntimeState: 'ready' | 'loading' | 'hidden' = 'ready';
	let contextWindowPreviewRequestId = 0;
	let contextWindowPreviewTimer: ReturnType<typeof setTimeout> | null = null;
	let contextWindowPreviewWatchKey = '';

	let chat = null;
	let tags = [];

	let history = {
		messages: {},
		currentId: null
	};

	let taskIds = null;

	// Chat Input
	let prompt = '';
	let chatFiles = [];
	let files = [];
	let params = {};
	let paramsHydratedFromSettings = false;
	let chatThinkingEnabled = false;
	let chatLedgerAgenticEnabled = false;
	let chatFocusedSearchEnabled = false;
	type WorkingMode = 'general' | 'medical' | 'general_science' | 'offsec' | 'news';
	const CHAT_WORKING_MODES: WorkingMode[] = [
		'general',
		'medical',
		'general_science',
		'offsec',
		'news'
	];
	let chatWorkingMode: WorkingMode = 'general';
	let chatLocalCorpusMode: 'off' | 'prefer' = 'off';
	let chatScienceResearchMode: ScienceResearchMode = 'light';
	let chatScienceAttachedCorpora: string[] = [];

	const cloneChatParams = (value: Record<string, unknown> | null | undefined) => {
		if (!value || typeof value !== 'object') {
			return {};
		}

		try {
			return structuredClone(value);
		} catch {
			return JSON.parse(JSON.stringify(value));
		}
	};

	const getDefaultChatParams = () => cloneChatParams($settings?.params ?? {});

	const initializeChatParams = (overrides: Record<string, unknown> = {}) => {
		params = { ...getDefaultChatParams(), ...cloneChatParams(overrides) };
		paramsHydratedFromSettings = $settings !== undefined;
	};

	$: if (!paramsHydratedFromSettings && $settings !== undefined) {
		params = { ...getDefaultChatParams(), ...(params ?? {}) };
		paramsHydratedFromSettings = true;
	}

	$: chatThinkingEnabled =
		(params?.custom_params?.chat_template_kwargs?.enable_thinking ?? false) === true;
	$: chatLedgerAgenticEnabled = (params?.ledger_mode ?? null) === 'agentic';
	$: chatFocusedSearchEnabled = (params?.focused_search_mode ?? false) === true;
	$: chatWorkingMode = CHAT_WORKING_MODES.includes(params?.working_mode ?? '')
		? params.working_mode
		: 'general';
	$: chatLocalCorpusMode = ['off', 'prefer'].includes(params?.local_corpus_mode ?? '')
		? params.local_corpus_mode
		: 'off';
	$: chatScienceResearchMode = normalizeScienceResearchMode(params?.science_research_mode);
	$: chatScienceAttachedCorpora = normalizeScienceAttachedCorpora(params?.science_attached_corpora);
	$: if (chatWorkingMode === 'general_science' && !$selectedTerminalId) {
		const scienceLaneTerminalId = resolveScienceLaneTerminalId({
			selectedTerminalId: $selectedTerminalId,
			systemTerminals: ($terminalServers ?? []).filter((terminal) => terminal?.id),
			directTerminals: ($settings?.terminalServers ?? []).filter((terminal) => terminal?.url)
		});

		if (scienceLaneTerminalId) {
			selectedTerminalId.set(scienceLaneTerminalId);
		}
	}

	const setChatThinkingEnabled = (enabled: boolean) => {
		const nextParams = JSON.parse(JSON.stringify(params ?? {}));
		const customParams =
			typeof nextParams.custom_params === 'object' && nextParams.custom_params !== null
				? nextParams.custom_params
				: {};
		const chatTemplateKwargs =
			typeof customParams.chat_template_kwargs === 'object' &&
			customParams.chat_template_kwargs !== null
				? customParams.chat_template_kwargs
				: {};

		if (enabled) {
			chatTemplateKwargs.enable_thinking = true;
			customParams.chat_template_kwargs = chatTemplateKwargs;
			nextParams.custom_params = customParams;
		} else {
			delete chatTemplateKwargs.enable_thinking;

			if (Object.keys(chatTemplateKwargs).length > 0) {
				customParams.chat_template_kwargs = chatTemplateKwargs;
			} else {
				delete customParams.chat_template_kwargs;
			}

			if (Object.keys(customParams).length > 0) {
				nextParams.custom_params = customParams;
			} else {
				delete nextParams.custom_params;
			}
		}

		params = nextParams;
	};

	const setChatLedgerAgenticEnabled = (enabled: boolean) => {
		const nextParams = JSON.parse(JSON.stringify(params ?? {}));
		if (enabled) {
			nextParams.ledger_mode = 'agentic';
		} else {
			delete nextParams.ledger_mode;
		}
		params = nextParams;
	};

	const setChatFocusedSearchEnabled = (enabled: boolean) => {
		const nextParams = JSON.parse(JSON.stringify(params ?? {}));
		if (enabled) {
			nextParams.focused_search_mode = true;
		} else {
			delete nextParams.focused_search_mode;
		}
		params = nextParams;
	};

	const setChatWorkingMode = (mode: WorkingMode) => {
		const nextParams = JSON.parse(JSON.stringify(params ?? {}));
		nextParams.working_mode = mode;
		if (mode === 'medical' || mode === 'general_science' || mode === 'offsec') {
			nextParams.local_corpus_mode = 'prefer';
		} else {
			nextParams.local_corpus_mode = 'off';
		}
		params = nextParams;
	};

	const setChatLocalCorpusMode = (mode: 'off' | 'prefer') => {
		const nextParams = JSON.parse(JSON.stringify(params ?? {}));
		nextParams.local_corpus_mode = mode;
		params = nextParams;
	};

	const setChatScienceResearchMode = (mode: ScienceResearchMode) => {
		const nextParams = JSON.parse(JSON.stringify(params ?? {}));
		nextParams.science_research_mode = mode;
		params = nextParams;
	};

	const setChatScienceAttachedCorpora = (corpora: string[]) => {
		const nextParams = JSON.parse(JSON.stringify(params ?? {}));
		nextParams.science_attached_corpora = normalizeScienceAttachedCorpora(corpora);
		params = nextParams;
	};

	const getCurrentPersonaSnapshot = () => {
		if (!selectedPersona) {
			return null;
		}

		return (
			getSelectedPersonaChatMeta()?.persona_defaults_snapshot ??
			buildPersonaDefaultsSnapshot(selectedPersona)
		);
	};

	const getCurrentPersonaOverrides = () => {
		if (!selectedPersona) {
			return {};
		}

		const snapshot = getCurrentPersonaSnapshot();
		if (!snapshot) {
			return {};
		}

		const overrides = {};
		const requestedFeatureIds = getRequestedFeatureIdsFromFeatures(getFeatures());

		if (JSON.stringify(selectedToolIds) !== JSON.stringify(snapshot.tool_ids ?? [])) {
			overrides['tool_ids'] = [...selectedToolIds];
		}

		if (JSON.stringify(selectedFilterIds) !== JSON.stringify(snapshot.filter_ids ?? [])) {
			overrides['filter_ids'] = [...selectedFilterIds];
		}

		if (
			JSON.stringify(requestedFeatureIds) !== JSON.stringify(snapshot.default_feature_ids ?? [])
		) {
			overrides['default_feature_ids'] = requestedFeatureIds;
		}

		const currentSystemPrompt = hasOwn(params, 'system') ? params.system : undefined;
		if (currentSystemPrompt !== snapshot.system_prompt) {
			overrides['system_prompt'] = hasOwn(params, 'system') ? params.system : null;
		}

		return overrides;
	};

	const getPersonaMetaForPersistence = () => {
		const nextMeta = selectedPersona
			? buildPersonaChatMeta(
					selectedPersona,
					getCurrentPersonaOverrides(),
					getSelectedPersonaChatMeta() ?? {},
					getCurrentPersonaSnapshot()
				)
			: (chat?.meta ?? null);

		if (sceneNote) {
			return {
				...nextMeta,
				scene_note: sceneNote
			};
		}

		const { scene_note, ...metaWithoutScene } = nextMeta ?? {};
		return metaWithoutScene;
	};

	const persistSceneNote = async (nextSceneNote: SceneNote | null) => {
		sceneNote = nextSceneNote;
		showSceneNoteModal = false;

		if (contextWindowRuntimeState === 'ready') {
			scheduleContextWindowPreviewRefresh(0, true);
		}

		if (!chat?.id || $temporaryChatEnabled) {
			return;
		}

		const updatedChat = await updateChatById(
			localStorage.token,
			chat.id,
			{
				models: normalizeModelSelection(selectedModels),
				history: history,
				messages: createMessagesList(history, history.currentId),
				params: params,
				files: chatFiles
			},
			getPersonaMetaForPersistence(),
			selectedPersonaId
		).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (updatedChat) {
			chat = updatedChat;
		}
	};

	const getContextWindowPreviewFileSignature = (items) =>
		JSON.stringify(
			(items ?? []).map((item) => ({
				id: item?.id ?? null,
				itemId: item?.itemId ?? null,
				name: item?.name ?? null,
				type: item?.type ?? null,
				status: item?.status ?? null,
				size: item?.size ?? null
			}))
		);

	const buildContextWindowPreviewMessages = () =>
		(history?.currentId ? createMessagesList(history, history.currentId) : []).filter((message) =>
			['user', 'assistant', 'tool'].includes(message?.role ?? '')
		);

	const resolveContextWindowRuntimeState = (
		modelIds: string[],
		availableModels: RuntimeAwareModel[]
	): 'ready' | 'loading' | 'hidden' => {
		const selected = modelIds
			.map((modelId) => availableModels.find((model) => model.id === modelId))
			.filter(Boolean);

		if (selected.length === 0) {
			return 'hidden';
		}

		const statuses = selected
			.map((model) => String(model?.status?.value ?? '').trim())
			.filter((status) => status.length > 0);

		if (statuses.length === 0) {
			return 'ready';
		}

		if (statuses.some((status) => status === 'loading')) {
			return 'loading';
		}

		if (statuses.every((status) => status === 'loaded')) {
			return 'ready';
		}

		return 'hidden';
	};

	const loadContextWindowPreview = async (force = false) => {
		const mainModelIds = selectedModelIds.filter((modelId) => modelId);
		if (loading || mainModelIds.length === 0 || contextWindowRuntimeState !== 'ready') {
			contextWindowPreview = null;
			return;
		}

		const currentMessageId = history?.currentId ?? null;
		const activeMessage = currentMessageId ? history?.messages?.[currentMessageId] : null;
		if (
			!force &&
			generating &&
			activeMessage?.role === 'assistant' &&
			activeMessage?.done !== true
		) {
			return;
		}

		const maintenanceEnabled =
			$settings?.contextMaintenance ?? $config?.features?.enable_context_maintenance ?? true;
		const systemMessage = (params?.system ?? $settings?.system ?? '').trim();
		const persistedChat = !!$chatId && !$chatId.startsWith('local:');
		const payload = {
			chat_id: persistedChat ? $chatId : null,
			current_message_id: persistedChat ? currentMessageId : null,
			main_model_ids: mainModelIds,
			messages: persistedChat ? undefined : buildContextWindowPreviewMessages(),
			files: files.length > 0 ? files : undefined,
			params,
			features: getFeatures(),
			system_message: systemMessage || undefined,
			context_maintenance_enabled: maintenanceEnabled
		};

		const requestId = ++contextWindowPreviewRequestId;

		try {
			const preview = await getContextWindowPreview(localStorage.token, payload);
			if (requestId === contextWindowPreviewRequestId) {
				contextWindowPreview = preview;
			}
		} catch (error) {
			if (requestId === contextWindowPreviewRequestId) {
				contextWindowPreview = null;
			}
		}
	};

	const scheduleContextWindowPreviewRefresh = (delay = 120, force = false) => {
		if (contextWindowPreviewTimer) {
			clearTimeout(contextWindowPreviewTimer);
			contextWindowPreviewTimer = null;
		}

		contextWindowPreviewTimer = setTimeout(() => {
			loadContextWindowPreview(force);
		}, delay);
	};
	// Message queue for storing messages while generating
	let messageQueue: { id: string; prompt: string; files: any[] }[] = [];
	let nextChatPersonaId: string | null | undefined = undefined;
	let nextChatDirectModels: string[] | null = null;

	$: if (chatIdProp) {
		navigateHandler();
	}

	const navigateHandler = async () => {
		loading = true;
		contextWindowPreview = null;
		contextWindowPreviewWatchKey = '';

		// Save current queue to sessionStorage before navigating away
		if (messageQueue.length > 0 && $chatId) {
			sessionStorage.setItem(`chat-queue-${$chatId}`, JSON.stringify(messageQueue));
		}

		prompt = '';
		messageInput?.setText('');
		sceneNote = null;
		showSceneNoteModal = false;

		files = [];
		messageQueue = [];
		selectedToolIds = [];
		selectedFilterIds = [];
		webSearchEnabled = false;
		imageGenerationEnabled = false;

		const storageChatInput = sessionStorage.getItem(
			`chat-input${chatIdProp ? `-${chatIdProp}` : ''}`
		);

		if (chatIdProp && (await loadChat())) {
			await tick();
			loading = false;
			window.setTimeout(() => scrollToBottom(), 0);

			await tick();

			// Restore queue from sessionStorage
			const storedQueueData = sessionStorage.getItem(`chat-queue-${chatIdProp}`);
			if (storedQueueData) {
				try {
					const restoredQueue = JSON.parse(storedQueueData);

					if (restoredQueue.length > 0) {
						sessionStorage.removeItem(`chat-queue-${chatIdProp}`);
						// Check if there are pending tasks (still generating)
						const hasPendingTask = taskIds !== null && taskIds.length > 0;
						if (!hasPendingTask) {
							// No pending tasks - process the queue
							files = restoredQueue.flatMap((m) => m.files);
							await tick();
							const combinedPrompt = restoredQueue.map((m) => m.prompt).join('\n\n');
							await submitPrompt(combinedPrompt);
						} else {
							// Has pending tasks - show as queued (chatCompletedHandler will process)
							messageQueue = restoredQueue;
						}
					}
				} catch (e) {}
			}

			if (storageChatInput) {
				try {
					const input = JSON.parse(storageChatInput);

					if (!$temporaryChatEnabled) {
						messageInput?.setText(input.prompt);
						files = input.files;
						selectedToolIds = input.selectedToolIds;
						selectedFilterIds = input.selectedFilterIds;
						webSearchEnabled = input.webSearchEnabled;
						imageGenerationEnabled = input.imageGenerationEnabled;
						codeInterpreterEnabled = input.codeInterpreterEnabled;
						const nextParams = getDefaultChatParams();
						if (
							typeof input.workingMode === 'string' &&
							CHAT_WORKING_MODES.includes(input.workingMode)
						) {
							nextParams.working_mode = input.workingMode;
						}
						if (typeof input.localCorpusMode === 'string') {
							nextParams.local_corpus_mode = ['off', 'prefer'].includes(input.localCorpusMode)
								? input.localCorpusMode
								: 'off';
						}
						nextParams.science_research_mode = normalizeScienceResearchMode(
							input.scienceResearchMode
						);
						nextParams.science_attached_corpora = normalizeScienceAttachedCorpora(
							input.scienceAttachedCorpora
						);
						initializeChatParams(nextParams);
					}
				} catch (e) {}
			} else {
				initializeChatParams();
				await setDefaults();
			}

			const chatInput = document.getElementById('chat-input');
			chatInput?.focus();
		} else {
			await goto('/');
		}
	};

	const onSelect = async (e) => {
		const { type, data } = e;

		if (type === 'prompt') {
			// Handle prompt selection
			messageInput?.setText(data, async () => {
				if (!($settings?.insertSuggestionPrompt ?? false)) {
					await tick();
					submitPrompt(prompt);
				}
			});
		}
	};

	$: if (selectedModels && chatIdProp !== '') {
		saveSessionSelectedModels();
	}

	const saveSessionSelectedModels = () => {
		if (selectedPersonaId) {
			return;
		}

		const selectedModelsString = JSON.stringify(selectedModels);
		if (
			selectedModels.length === 0 ||
			(selectedModels.length === 1 && selectedModels[0] === '') ||
			sessionStorage.selectedModels === selectedModelsString
		) {
			return;
		}
		sessionStorage.selectedModels = selectedModelsString;
		console.log('saveSessionSelectedModels', selectedModels, sessionStorage.selectedModels);
	};

	const applyDirectModelSelection = () => {
		selectedPersonaId = null;
		sceneNote = null;
		showSceneNoteModal = false;
		atSelectedModel = undefined;
		selectedModels =
			directSelectedModels.filter((modelId) => modelId).length > 0
				? [...directSelectedModels]
				: [...($settings?.models ?? selectedModels)];
		resetInput();
	};

	const applyPersonaSelectionInPlace = (personaId: string | null) => {
		selectedPersonaId = personaId;
		sceneNote = null;
		showSceneNoteModal = false;
		if (!personaId) {
			applyDirectModelSelection();
			return;
		}

		const persona = ($personas ?? []).find((item) => item.id === personaId);
		if (persona?.bound_model_id) {
			selectedModels = [persona.bound_model_id];
		}

		resetInput();
	};

	const handlePersonaSelect = async (personaId: string | null) => {
		const nextPersonaId = personaId || null;
		if ((selectedPersonaId ?? null) === nextPersonaId) {
			return;
		}

		const hasHistory = createMessagesList(history, history.currentId).length > 0;
		if (!hasHistory) {
			applyPersonaSelectionInPlace(nextPersonaId);
			return;
		}

		const draftText = prompt;
		const previousPersonaId = selectedPersonaId ?? null;
		const previousDirectModels = [...directSelectedModels];

		eventConfirmationTitle = $i18n.t('Start new chat?');
		eventConfirmationMessage = $i18n.t(
			'Switching persona or moving to Direct Model starts a new chat. Only the current draft will be carried over.'
		);
		showEventConfirmation = true;
		eventCallback = async (confirmed) => {
			showEventConfirmation = false;
			if (!confirmed) {
				selectedPersonaId = previousPersonaId;
				directSelectedModels = previousDirectModels;
				return;
			}

			nextChatPersonaId = nextPersonaId;
			nextChatDirectModels = nextPersonaId ? null : previousDirectModels;
			await initNewChat();
			if (draftText) {
				messageInput?.setText(draftText);
			}
		};
	};

	let oldSelectedModelIds = [''];
	$: if (JSON.stringify(selectedModelIds) !== JSON.stringify(oldSelectedModelIds)) {
		onSelectedModelIdsChange();
	}

	$: contextWindowPreviewDependencyKey = JSON.stringify({
		chatId: $chatId ?? null,
		currentId: history?.currentId ?? null,
		modelIds: selectedModelIds.filter((modelId) => modelId),
		modelStatuses: selectedModelIds
			.filter((modelId) => modelId)
			.map((modelId) => {
				const model = ($models as RuntimeAwareModel[]).find((entry) => entry.id === modelId);
				return [modelId, model?.status?.value ?? null];
			}),
		files: getContextWindowPreviewFileSignature(files),
		maintenance:
			$settings?.contextMaintenance ?? $config?.features?.enable_context_maintenance ?? true,
		system: params?.system ?? $settings?.system ?? ''
	});

	$: contextWindowRuntimeState = resolveContextWindowRuntimeState(
		selectedModelIds.filter((modelId) => modelId),
		$models as RuntimeAwareModel[]
	);

	$: if (
		!loading &&
		contextWindowPreviewDependencyKey !== contextWindowPreviewWatchKey &&
		selectedModelIds.filter((modelId) => modelId).length > 0 &&
		contextWindowRuntimeState === 'ready'
	) {
		contextWindowPreviewWatchKey = contextWindowPreviewDependencyKey;
		scheduleContextWindowPreviewRefresh();
	}

	$: if (
		selectedModelIds.filter((modelId) => modelId).length === 0 ||
		contextWindowRuntimeState !== 'ready'
	) {
		contextWindowPreview = null;
	}

	const onSelectedModelIdsChange = () => {
		resetInput();
		oldSelectedModelIds = structuredClone(selectedModelIds);
	};

	const resetInput = () => {
		selectedToolIds = [];
		selectedFilterIds = [];
		webSearchEnabled = false;
		imageGenerationEnabled = false;
		codeInterpreterEnabled = false;

		if (selectedModelIds.filter((id) => id).length > 0) {
			setDefaults();
		}
	};

	const setDefaults = async () => {
		if (!$tools) {
			tools.set(await getTools(localStorage.token));
		}
		if (!$functions) {
			functions.set(await getFunctions(localStorage.token));
		}
		if (selectedModels.length !== 1 && !atSelectedModel) {
			return;
		}

		const model = atSelectedModel ?? $models.find((m) => m.id === selectedModels[0]);
		if (model) {
			const personaState = selectedPersona
				? getEffectivePersonaState({
						persona: selectedPersona,
						chatMeta: chat?.meta ?? null,
						model,
						tools: $tools ?? [],
						functions: $functions ?? [],
						config: $config,
						user: $user
					})
				: null;

			// Set Default Tools
			if (model?.info?.meta?.toolIds) {
				selectedToolIds = [
					...new Set(
						[...(model?.info?.meta?.toolIds ?? [])].filter((id) => $tools.find((t) => t.id === id))
					)
				];
			} else if ($settings?.tools) {
				selectedToolIds = $settings.tools;
			} else {
				selectedToolIds = selectedToolIds.filter((id) => !id.startsWith('direct_server:'));
			}

			// Set Default Filters (Toggleable only)
			if (model?.info?.meta?.defaultFilterIds) {
				selectedFilterIds = model.info.meta.defaultFilterIds.filter((id) =>
					model?.filters?.find((f) => f.id === id)
				);
			}

			// Set Default Features
			if (model?.info?.meta?.defaultFeatureIds) {
				if (
					model.info?.meta?.capabilities?.['image_generation'] &&
					$config?.features?.enable_image_generation &&
					($user?.role === 'admin' || $user?.permissions?.features?.image_generation)
				) {
					imageGenerationEnabled = model.info.meta.defaultFeatureIds.includes('image_generation');
				}

				if (
					model.info?.meta?.capabilities?.['web_search'] &&
					$config?.features?.enable_web_search &&
					($user?.role === 'admin' || $user?.permissions?.features?.web_search)
				) {
					webSearchEnabled = model.info.meta.defaultFeatureIds.includes('web_search');
				}

				if (
					model.info?.meta?.capabilities?.['code_interpreter'] &&
					$config?.features?.enable_code_interpreter &&
					($user?.role === 'admin' || $user?.permissions?.features?.code_interpreter)
				) {
					codeInterpreterEnabled = model.info.meta.defaultFeatureIds.includes('code_interpreter');
				}
			}

			if (selectedPersona) {
				const nextParams = structuredClone(params ?? {});
				const effectiveSystemPrompt = personaState?.effective?.system_prompt;

				if (effectiveSystemPrompt !== null && effectiveSystemPrompt !== undefined) {
					nextParams.system = effectiveSystemPrompt;
				} else {
					delete nextParams.system;
				}

				params = nextParams;
			}
		}
	};

	const showMessage = async (message, scroll = true) => {
		const _chatId = JSON.parse(JSON.stringify($chatId));
		let _messageId = JSON.parse(JSON.stringify(message.id));

		let messageChildrenIds = [];
		if (_messageId === null) {
			messageChildrenIds = Object.keys(history.messages).filter(
				(id) => history.messages[id].parentId === null
			);
		} else {
			messageChildrenIds = history.messages[_messageId].childrenIds;
		}

		while (messageChildrenIds.length !== 0) {
			_messageId = messageChildrenIds.at(-1);
			messageChildrenIds = history.messages[_messageId].childrenIds;
		}

		history.currentId = _messageId;

		await tick();

		if (($settings?.scrollOnBranchChange ?? true) && scroll) {
			const messageElement = document.getElementById(`message-${message.id}`);
			if (messageElement) {
				messageElement.scrollIntoView({ behavior: 'smooth', block: 'start' });
			}
		}

		await tick();
		await tick();
		await tick();

		saveChatHandler(_chatId, history);
	};

	const terminalEventHandler = (type: string, data: any) => {
		if (type === 'terminal:display_file') {
			if (!data?.path) return;
			displayFileHandler(data.path, { showControls, showFileNavPath });
		} else if (type === 'terminal:write_file' || type === 'terminal:replace_file_content') {
			if (!data?.path) return;
			showFileNavDir.set(data.path);
		} else if (type === 'terminal:run_command') {
			showFileNavDir.set('/');
		}
	};

	const chatEventHandler = async (event, cb) => {
		console.log(event);

		if (event.chat_id === $chatId) {
			await tick();
			let message = history.messages[event.message_id];

			if (message) {
				const type = event?.data?.type ?? null;
				const data = event?.data?.data ?? null;

				if (type === 'status') {
					if (message?.statusHistory) {
						message.statusHistory.push(data);
					} else {
						message.statusHistory = [data];
					}

					if (data?.action === 'context_maintenance' && data?.done === true) {
						scheduleContextWindowPreviewRefresh(0, true);
					}
				} else if (type === 'chat:completion') {
					chatCompletionEventHandler(data, message, event.chat_id);
				} else if (type === 'chat:tasks:cancel') {
					if (event.message_id === history.currentId) {
						taskIds = null;
						// Set all response messages to done
						for (const messageId of history.messages[message.parentId].childrenIds) {
							history.messages[messageId].done = true;
						}
					} else {
						message.done = true;
					}
				} else if (type === 'chat:message:delta' || type === 'message') {
					message.content += data.content;
				} else if (type === 'chat:message' || type === 'replace') {
					message.content = data.content;
				} else if (type === 'chat:message:files' || type === 'files') {
					message.files = data.files;
				} else if (type === 'chat:message:embeds' || type === 'embeds') {
					message.embeds = data.embeds;

					// Auto-scroll to the embed once it's rendered in the DOM
					await tick();
					setTimeout(() => {
						const embedEl = document.getElementById(`${event.message_id}-embeds-container`);
						if (embedEl) {
							embedEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
						}
					}, 100);
				} else if (type === 'chat:message:error') {
					message.error = data.error;
				} else if (type === 'chat:message:follow_ups') {
					message.followUps = data.follow_ups;

					if (autoScroll) {
						scrollToBottom('smooth');
					}
				} else if (type === 'chat:message:favorite') {
					// Update message favorite status
					message.favorite = data.favorite;
				} else if (type === 'chat:title') {
					chatTitle.set(data);
					currentChatPage.set(1);
					await chats.set(await getChatList(localStorage.token, $currentChatPage));
				} else if (type === 'chat:tags') {
					chat = await getChatById(localStorage.token, $chatId);
					allTags.set(await getAllTags(localStorage.token));
				} else if (type === 'source' || type === 'citation') {
					if (data?.type === 'code_execution') {
						// Code execution; update existing code execution by ID, or add new one.
						if (!message?.code_executions) {
							message.code_executions = [];
						}

						const existingCodeExecutionIndex = message.code_executions.findIndex(
							(execution) => execution.id === data.id
						);

						if (existingCodeExecutionIndex !== -1) {
							message.code_executions[existingCodeExecutionIndex] = data;
						} else {
							message.code_executions.push(data);
						}

						message.code_executions = message.code_executions;
					} else {
						// Regular source.
						if (message?.sources) {
							message.sources.push(data);
						} else {
							message.sources = [data];
						}
					}
				} else if (type === 'notification') {
					const toastType = data?.type ?? 'info';
					const toastContent = data?.content ?? '';

					if (toastType === 'success') {
						toast.success(toastContent);
					} else if (toastType === 'error') {
						toast.error(toastContent);
					} else if (toastType === 'warning') {
						toast.warning(toastContent);
					} else {
						toast.info(toastContent);
					}
				} else if (type === 'confirmation') {
					eventCallback = cb;

					eventConfirmationInput = false;
					showEventConfirmation = true;

					eventConfirmationTitle = data.title;
					eventConfirmationMessage = data.message;
				} else if (type === 'execute') {
					eventCallback = cb;

					try {
						// Use Function constructor to evaluate code in a safer way
						const asyncFunction = new Function(`return (async () => { ${data.code} })()`);
						const result = await asyncFunction(); // Await the result of the async function

						if (cb) {
							cb(result);
						}
					} catch (error) {
						console.error('Error executing code:', error);
					}
				} else if (type === 'input') {
					eventCallback = cb;

					eventConfirmationInput = true;
					showEventConfirmation = true;

					eventConfirmationTitle = data.title;
					eventConfirmationMessage = data.message;
					eventConfirmationInputPlaceholder = data.placeholder;
					eventConfirmationInputValue = data?.value ?? '';
					eventConfirmationInputType = data?.type ?? '';
				} else if (type.startsWith('terminal:')) {
					terminalEventHandler(type, data);
				} else {
					console.log('Unknown message type', data);
				}

				history.messages[event.message_id] = message;
			}
		}
	};

	const onMessageHandler = async (event: {
		origin: string;
		data: { type: string; text: string };
	}) => {
		const isSameOrigin = event.origin === window.origin;
		const type = event.data?.type;

		// Prompt-related message types only submit text to the chat input —
		// functionally equivalent to the user typing.  When same-origin is
		// enabled they go through immediately.  When it is disabled (opaque
		// origin) we show a confirmation dialog so the user stays in control.
		const iframePromptTypes = ['input:prompt', 'input:prompt:submit', 'action:submit'];

		if (!isSameOrigin && !iframePromptTypes.includes(type)) {
			return;
		}

		if (type === 'action:submit') {
			console.debug(event.data.text);

			if (prompt !== '') {
				await tick();
				submitPrompt(prompt);
			}
		}

		if (type === 'input:prompt') {
			console.debug(event.data.text);

			const inputElement = document.getElementById('chat-input');

			if (inputElement) {
				messageInput?.setText(event.data.text);
				inputElement.focus();
			}
		}

		if (type === 'input:prompt:submit') {
			console.debug(event.data.text);

			if (event.data.text !== '') {
				if (isSameOrigin) {
					await tick();
					submitPrompt(event.data.text);
				} else {
					// Cross-origin: ask user to confirm before submitting
					eventConfirmationInput = false;
					eventConfirmationTitle = $i18n.t('Confirm Prompt from Embed');
					eventConfirmationMessage = event.data.text;
					eventCallback = async (confirmed: boolean) => {
						if (confirmed) {
							await tick();
							submitPrompt(event.data.text);
						}
					};
					showEventConfirmation = true;
				}
			}
		}
	};

	const savedModelIds = async () => {
		if (selectedPersonaId) {
			return;
		}

		if (
			$selectedFolder &&
			selectedModels.filter((modelId) => modelId !== '').length > 0 &&
			JSON.stringify($selectedFolder?.data?.model_ids) !==
				JSON.stringify(normalizeModelSelection(selectedModels))
		) {
			const res = await updateFolderById(localStorage.token, $selectedFolder.id, {
				data: {
					model_ids: normalizeModelSelection(selectedModels)
				}
			});
		}
	};

	$: if (selectedModels !== null) {
		savedModelIds();
	}

	const stopAudio = () => {
		try {
			speechSynthesis.cancel();
			$audioQueue?.stop();
		} catch {}
	};

	onMount(() => {
		loading = true;
		console.log('mounted');
		window.addEventListener('message', onMessageHandler);
		$socket?.on('events', chatEventHandler);

		$audioQueue?.destroy();

		const audioQueueInstance = new AudioQueue(document.getElementById('audioElement'));
		audioQueue.set(audioQueueInstance);

		// Restore direct terminal enabled states based on persisted selectedTerminalId
		if ($settings?.terminalServers?.length) {
			settings.set({
				...$settings,
				terminalServers: ($settings.terminalServers ?? []).map((s) => ({
					...s,
					enabled: $selectedTerminalId !== null && s.url === $selectedTerminalId
				}))
			});
		}

		const pageSubscribe = page.subscribe(async (p) => {
			if (p.url.pathname === '/') {
				await tick();
				initNewChat();

				// Re-fetch banners on navigation to homepage so newly configured banners appear
				try {
					banners.set(await getBanners(localStorage.token).catch(() => []));
				} catch (e) {
					console.error('Failed to refresh banners:', e);
				}
			}

			stopAudio();
		});

		const showControlsSubscribe = showControls.subscribe(async (value) => {
			await tick();
			if (controlPane && !$mobile) {
				try {
					if (value) {
						controlPaneComponent?.openPane();
					} else {
						controlPane.collapse();
					}
				} catch (e) {
					// ignore
				}
			}

			if (!value) {
				showCallOverlay.set(false);
				showArtifacts.set(false);
				showEmbeds.set(false);
			}
		});

		const selectedFolderSubscribe = selectedFolder.subscribe(async (folder) => {
			await tick();
			if (
				folder?.data?.model_ids &&
				JSON.stringify(selectedModels) !==
					JSON.stringify(normalizeModelSelection(folder.data.model_ids))
			) {
				selectedModels = normalizeModelSelection(folder.data.model_ids);

				console.log('Set selectedModels from folder data:', selectedModels);
			}
		});

		const storageChatInput = sessionStorage.getItem(
			`chat-input${chatIdProp ? `-${chatIdProp}` : ''}`
		);

		const init = async () => {
			if (!chatIdProp) {
				loading = false;
				await tick();
			}

			if (storageChatInput) {
				prompt = '';
				messageInput?.setText('');

				files = [];
				selectedToolIds = [];
				selectedFilterIds = [];
				webSearchEnabled = false;
				imageGenerationEnabled = false;
				codeInterpreterEnabled = false;

				try {
					const input = JSON.parse(storageChatInput);

					if (!$temporaryChatEnabled) {
						messageInput?.setText(input.prompt);
						files = input.files;
						selectedToolIds = input.selectedToolIds;
						selectedFilterIds = input.selectedFilterIds;
						webSearchEnabled = input.webSearchEnabled;
						imageGenerationEnabled = input.imageGenerationEnabled;
						codeInterpreterEnabled = input.codeInterpreterEnabled;
						const nextParams = getDefaultChatParams();
						if (
							typeof input.workingMode === 'string' &&
							CHAT_WORKING_MODES.includes(input.workingMode)
						) {
							nextParams.working_mode = input.workingMode;
						}
						if (typeof input.localCorpusMode === 'string') {
							nextParams.local_corpus_mode = ['off', 'prefer'].includes(input.localCorpusMode)
								? input.localCorpusMode
								: 'off';
						}
						nextParams.science_research_mode = normalizeScienceResearchMode(
							input.scienceResearchMode
						);
						nextParams.science_attached_corpora = normalizeScienceAttachedCorpora(
							input.scienceAttachedCorpora
						);
						initializeChatParams(nextParams);
					}
				} catch (e) {}
			}

			const chatInput = document.getElementById('chat-input');
			chatInput?.focus();
		};
		init();

		return () => {
			try {
				if (contextWindowPreviewTimer) {
					clearTimeout(contextWindowPreviewTimer);
					contextWindowPreviewTimer = null;
				}
				pageSubscribe();
				showControlsSubscribe();
				selectedFolderSubscribe();
				window.removeEventListener('message', onMessageHandler);
				$socket?.off('events', chatEventHandler);
				audioQueueInstance?.destroy();
				audioQueue.set(null);
			} catch (e) {
				console.error(e);
			}
		};
	});

	// File upload functions

	const uploadGoogleDriveFile = async (fileData) => {
		console.log('Starting uploadGoogleDriveFile with:', {
			id: fileData.id,
			name: fileData.name,
			url: fileData.url,
			headers: {
				Authorization: `Bearer ${token}`
			}
		});

		// Validate input
		if (!fileData?.id || !fileData?.name || !fileData?.url || !fileData?.headers?.Authorization) {
			throw new Error('Invalid file data provided');
		}

		const tempItemId = uuidv4();
		const fileItem = {
			type: 'file',
			file: '',
			id: null,
			url: fileData.url,
			name: fileData.name,
			collection_name: '',
			status: 'uploading',
			error: '',
			itemId: tempItemId,
			size: 0
		};

		try {
			files = [...files, fileItem];
			console.log('Processing web file with URL:', fileData.url);

			// Configure fetch options with proper headers
			const fetchOptions = {
				headers: {
					Authorization: fileData.headers.Authorization,
					Accept: '*/*'
				},
				method: 'GET'
			};

			// Attempt to fetch the file
			console.log('Fetching file content from Google Drive...');
			const fileResponse = await fetch(fileData.url, fetchOptions);

			if (!fileResponse.ok) {
				const errorText = await fileResponse.text();
				throw new Error(`Failed to fetch file (${fileResponse.status}): ${errorText}`);
			}

			// Get content type from response
			const contentType = fileResponse.headers.get('content-type') || 'application/octet-stream';
			console.log('Response received with content-type:', contentType);

			// Convert response to blob
			console.log('Converting response to blob...');
			const fileBlob = await fileResponse.blob();

			if (fileBlob.size === 0) {
				throw new Error('Retrieved file is empty');
			}

			console.log('Blob created:', {
				size: fileBlob.size,
				type: fileBlob.type || contentType
			});

			// Create File object with proper MIME type
			const file = new File([fileBlob], fileData.name, {
				type: fileBlob.type || contentType
			});

			console.log('File object created:', {
				name: file.name,
				size: file.size,
				type: file.type
			});

			if (file.size === 0) {
				throw new Error('Created file is empty');
			}

			// If the file is an audio file, provide the language for STT.
			let metadata = null;
			if (
				(file.type.startsWith('audio/') || file.type.startsWith('video/')) &&
				$settings?.audio?.stt?.language
			) {
				metadata = {
					language: $settings?.audio?.stt?.language
				};
			}

			// Upload file to server
			console.log('Uploading file to server...');
			const uploadedFile = await uploadFile(localStorage.token, file, metadata);

			if (!uploadedFile) {
				throw new Error('Server returned null response for file upload');
			}

			console.log('File uploaded successfully:', uploadedFile);

			// Update file item with upload results
			fileItem.status = 'uploaded';
			fileItem.file = uploadedFile;
			fileItem.id = uploadedFile.id;
			fileItem.size = file.size;
			fileItem.collection_name = uploadedFile?.meta?.collection_name;
			fileItem.url = `${uploadedFile.id}`;

			files = files;
			toast.success($i18n.t('File uploaded successfully'));
		} catch (e) {
			console.error('Error uploading file:', e);
			files = files.filter((f) => f.itemId !== tempItemId);
			toast.error(
				$i18n.t('Error uploading file: {{error}}', {
					error: e.message || 'Unknown error'
				})
			);
		}
	};

	const uploadWeb = async (urls) => {
		if ($user?.role !== 'admin' && !($user?.permissions?.chat?.web_upload ?? true)) {
			toast.error($i18n.t('You do not have permission to upload web content.'));
			return;
		}

		if (!Array.isArray(urls)) {
			urls = [urls];
		}

		// Create file items first
		const fileItems = urls.map((url) => ({
			type: 'text',
			name: url,
			collection_name: '',
			status: 'uploading',
			context: 'full',
			url,
			error: ''
		}));

		// Display all items at once
		files = [...files, ...fileItems];

		for (const fileItem of fileItems) {
			try {
				const res = isYoutubeUrl(fileItem.url)
					? await processYoutubeVideo(localStorage.token, fileItem.url)
					: await processWeb(localStorage.token, '', fileItem.url);

				if (res) {
					fileItem.status = 'uploaded';
					fileItem.collection_name = res.collection_name;
					fileItem.file = {
						...res.file,
						...fileItem.file
					};
				}

				files = [...files];
			} catch (e) {
				files = files.filter((f) => f.name !== url);
				toast.error(`${e}`);
			}
		}
	};

	const onUpload = async (event) => {
		const { type, data } = event;

		if (type === 'google-drive') {
			await uploadGoogleDriveFile(data);
		} else if (type === 'web') {
			await uploadWeb(data);
		}
	};

	const onHistoryChange = (history) => {
		if (history) {
			cancelAnimationFrame(contentsRAF);
			contentsRAF = requestAnimationFrame(() => {
				getContents();
				contentsRAF = null;
			});
		} else {
			artifactContents.set([]);
		}
	};

	$: onHistoryChange(history);

	const getContents = () => {
		const messages = history ? createMessagesList(history, history.currentId) : [];
		let contents = [];
		messages.forEach((message) => {
			if (message?.role !== 'user' && message?.content) {
				const {
					codeBlocks: codeBlocks,
					html: htmlContent,
					css: cssContent,
					js: jsContent
				} = getCodeBlockContents(message.content);

				if (htmlContent || cssContent || jsContent) {
					const renderedContent = `
                        <!DOCTYPE html>
                        <html lang="en">
                        <head>
                            <meta charset="UTF-8">
                            <meta name="viewport" content="width=device-width, initial-scale=1.0">
							<${''}style>
								body {
									background-color: white; /* Ensure the iframe has a white background */
								}

								${cssContent}
							</${''}style>
                        </head>
                        <body>
                            ${htmlContent}

							<${''}script>
                            	${jsContent}
							</${''}script>
                        </body>
                        </html>
                    `;
					contents = [...contents, { type: 'iframe', content: renderedContent }];
				} else {
					// Check for SVG content
					for (const block of codeBlocks) {
						if (block.lang === 'svg' || (block.lang === 'xml' && block.code.includes('<svg'))) {
							contents = [...contents, { type: 'svg', content: block.code }];
						}
					}
				}
			}
		});

		artifactContents.set(contents);
	};

	//////////////////////////
	// Web functions
	//////////////////////////

	const initNewChat = async () => {
		console.log('initNewChat');
		if ($user?.role !== 'admin' && $user?.permissions?.chat?.temporary_enforced) {
			await temporaryChatEnabled.set(true);
		}

		if ($settings?.temporaryChatByDefault ?? false) {
			if ($temporaryChatEnabled === false) {
				await temporaryChatEnabled.set(true);
			} else if ($temporaryChatEnabled === null) {
				// if set to null set to false; refer to temp chat toggle click handler
				await temporaryChatEnabled.set(false);
			}
		}

		if ($user?.role !== 'admin' && !$user?.permissions?.chat?.temporary) {
			await temporaryChatEnabled.set(false);
		}

		const availableModels = $models
			.filter((m) => !(m?.info?.meta?.hidden ?? false))
			.map((m) => m.id);
		const availablePersonas = ($personas ?? []).filter((persona) => persona.is_active);
		const requestedDirectModels =
			nextChatDirectModels && nextChatDirectModels.length > 0
				? normalizeModelSelection(nextChatDirectModels)
				: null;

		const defaultModels = $config?.default_models
			? normalizeModelSelection($config?.default_models.split(','))
			: [];
		const urlPersonaId = $page.url.searchParams.get('persona');
		const defaultPersonaId =
			nextChatPersonaId !== undefined
				? nextChatPersonaId
				: (availablePersonas.find((persona) => persona.id === urlPersonaId)?.id ??
					availablePersonas.find((persona) => persona.id === $settings?.personaId)?.id ??
					null);

		nextChatPersonaId = undefined;

		if (defaultPersonaId) {
			selectedPersonaId = defaultPersonaId;
			const persona = availablePersonas.find((item) => item.id === defaultPersonaId);
			if (persona?.bound_model_id && availableModels.includes(persona.bound_model_id)) {
				selectedModels = [persona.bound_model_id];
			} else {
				selectedPersonaId = null;
			}
		} else {
			selectedPersonaId = null;
		}

		if (!selectedPersonaId && requestedDirectModels) {
			selectedModels = normalizeModelSelection(requestedDirectModels);
		} else if (
			!selectedPersonaId &&
			($page.url.searchParams.get('models') || $page.url.searchParams.get('model'))
		) {
			const urlModels = (
				$page.url.searchParams.get('models') ||
				$page.url.searchParams.get('model') ||
				''
			)?.split(',');
			const normalizedUrlModels = normalizeModelSelection(urlModels);

			if (normalizedUrlModels.length === 1) {
				if (!$models.find((m) => m.id === normalizedUrlModels[0])) {
					// Model not found; open model selector and prefill
					const modelSelectorButton = document.getElementById('model-selector-0-button');
					if (modelSelectorButton) {
						modelSelectorButton.click();
						await tick();

						const modelSelectorInput = document.getElementById('model-search-input');
						if (modelSelectorInput) {
							modelSelectorInput.focus();
							modelSelectorInput.value = normalizedUrlModels[0];
							modelSelectorInput.dispatchEvent(new Event('input'));
						}
					}
				} else {
					// Model found; set it as selected
					selectedModels = normalizedUrlModels;
				}
			} else {
				// Multiple models; set as selected
				selectedModels = normalizedUrlModels;
			}

			// Unavailable models filtering
			selectedModels = normalizeModelSelection(
				selectedModels.filter((modelId) => $models.map((m) => m.id).includes(modelId))
			);
		} else if (!selectedPersonaId) {
			if ($selectedFolder?.data?.model_ids) {
				// Set from folder model IDs
				selectedModels = normalizeModelSelection($selectedFolder?.data?.model_ids);
			} else {
				if (sessionStorage.selectedModels) {
					// Set from session storage (temporary selection)
					selectedModels = normalizeModelSelection(JSON.parse(sessionStorage.selectedModels));
					sessionStorage.removeItem('selectedModels');
				} else {
					if ($settings?.models) {
						// Set from user settings
						selectedModels = normalizeModelSelection($settings?.models);
					} else if (defaultModels && defaultModels.length > 0) {
						// Set from default models
						selectedModels = defaultModels;
					}
				}
			}

			// Unavailable & hidden models filtering
			selectedModels = normalizeModelSelection(
				selectedModels.filter((modelId) => availableModels.includes(modelId))
			);
		}
		nextChatDirectModels = null;

		// Ensure at least one model is selected
		if (
			!selectedPersonaId &&
			(selectedModels.length === 0 || (selectedModels.length === 1 && selectedModels[0] === ''))
		) {
			if (availableModels.length > 0) {
				if (defaultModels && defaultModels.length > 0) {
					selectedModels = defaultModels.filter((modelId) => availableModels.includes(modelId));
				}

				if (
					selectedModels.length === 0 ||
					(selectedModels.length === 1 && selectedModels[0] === '')
				) {
					// Only fall back to first available model if default models didn't resolve
					selectedModels = [availableModels?.at(0) ?? ''];
				}
			} else {
				selectedModels = [''];
			}
		}

		if (!selectedPersonaId) {
			directSelectedModels = [...selectedModels];
		}

		if ($mobile) {
			await showControls.set(false);
		}
		await showCallOverlay.set(false);
		await showArtifacts.set(false);

		if ($page.url.pathname.includes('/c/')) {
			window.history.replaceState(history.state, '', `/`);
		}

		autoScroll = true;

		resetInput();
		sceneNote = null;
		showSceneNoteModal = false;
		await chatId.set('');
		await chatTitle.set('');

		history = {
			messages: {},
			currentId: null
		};
		chat = null;
		tags = [];

		chatFiles = [];
		initializeChatParams();
		taskIds = null;
		messageQueue = [];

		if ($page.url.searchParams.get('youtube')) {
			await uploadWeb(`https://www.youtube.com/watch?v=${$page.url.searchParams.get('youtube')}`);
		}

		if ($page.url.searchParams.get('load-url')) {
			await uploadWeb($page.url.searchParams.get('load-url'));
		}

		if ($page.url.searchParams.get('web-search') === 'true') {
			webSearchEnabled = true;
		}

		if ($page.url.searchParams.get('image-generation') === 'true') {
			imageGenerationEnabled = true;
		}

		if ($page.url.searchParams.get('code-interpreter') === 'true') {
			codeInterpreterEnabled = true;
		}

		if ($page.url.searchParams.get('tools')) {
			selectedToolIds = $page.url.searchParams
				.get('tools')
				?.split(',')
				.map((id) => id.trim())
				.filter((id) => id);
		} else if ($page.url.searchParams.get('tool-ids')) {
			selectedToolIds = $page.url.searchParams
				.get('tool-ids')
				?.split(',')
				.map((id) => id.trim())
				.filter((id) => id);
		}

		if ($page.url.searchParams.get('call') === 'true') {
			showCallOverlay.set(true);
			showControls.set(true);
		}

		if ($page.url.searchParams.get('q')) {
			const q = $page.url.searchParams.get('q') ?? '';
			messageInput?.setText(q);

			if (q) {
				if (($page.url.searchParams.get('submit') ?? 'true') === 'true') {
					await tick();
					submitPrompt(q);
				}
			}
		}

		selectedModels = normalizeModelSelection(
			selectedModels.map((modelId) => ($models.map((m) => m.id).includes(modelId) ? modelId : '')),
			{ preserveEmpty: true }
		);

		const chatInput = document.getElementById('chat-input');
		setTimeout(() => chatInput?.focus(), 0);
	};

	const loadChat = async () => {
		chatId.set(chatIdProp);

		if ($temporaryChatEnabled) {
			temporaryChatEnabled.set(false);
		}

		chat = await getChatById(localStorage.token, $chatId).catch(async (error) => {
			await goto('/');
			return null;
		});

		if (chat) {
			tags = await getTagsById(localStorage.token, $chatId).catch(async (error) => {
				return [];
			});

			const chatContent = chat.chat;

			if (chatContent) {
				console.log(chatContent);

				selectedPersonaId = chat.persona_id ?? null;
				sceneNote = normalizeSceneNote(chat?.meta?.scene_note ?? null);

				const persistedPersona = selectedPersonaId
					? (($personas ?? []).find((persona) => persona.id === selectedPersonaId) ?? null)
					: null;
				const persistedPersonaSnapshot = chat?.meta?.persona_defaults_snapshot ?? null;
				const personaBoundModelId =
					persistedPersonaSnapshot?.bound_model_id ?? persistedPersona?.bound_model_id ?? null;

				selectedModels = normalizeModelSelection(
					selectedPersonaId
						? personaBoundModelId
							? [personaBoundModelId]
							: (chatContent?.models ?? undefined) !== undefined
								? chatContent.models
								: [chatContent.models ?? '']
						: (chatContent?.models ?? undefined) !== undefined
							? chatContent.models
							: [chatContent.models ?? '']
				);

				if (!($user?.role === 'admin' || ($user?.permissions?.chat?.multiple_models ?? true))) {
					selectedModels = selectedModels.length > 0 ? [selectedModels[0]] : [''];
				}

				if (!selectedPersonaId) {
					directSelectedModels = [...selectedModels];
				}

				oldSelectedModelIds = structuredClone(selectedModels);

				history = normalizeHistoryModelSelections(
					(chatContent?.history ?? undefined) !== undefined
						? chatContent.history
						: convertMessagesToHistory(chatContent.messages)
				);

				chatTitle.set(chatContent.title);

				initializeChatParams(chatContent?.params ?? {});
				chatFiles = chatContent?.files ?? [];

				autoScroll = true;
				await tick();

				if (history.currentId) {
					for (const message of Object.values(history.messages)) {
						if (message && message.role === 'assistant') {
							message.done = true;
						}
					}
				}

				const taskRes = await getTaskIdsByChatId(localStorage.token, $chatId).catch((error) => {
					return null;
				});

				if (taskRes) {
					taskIds = taskRes.task_ids;
				}

				await tick();

				return true;
			} else {
				return null;
			}
		}
	};

	const scrollToBottom = async (behavior = 'auto') => {
		await tick();
		if (messagesContainerElement) {
			messagesContainerElement.scrollTo({
				top: messagesContainerElement.scrollHeight,
				behavior
			});
		}
	};

	let scrollRAF = null;
	let contentsRAF = null;
	const scheduleScrollToBottom = () => {
		if (!scrollRAF) {
			scrollRAF = requestAnimationFrame(async () => {
				scrollRAF = null;
				await scrollToBottom();
			});
		}
	};
	const chatCompletedHandler = async (_chatId, modelId, responseMessageId, messages) => {
		const res = await chatCompleted(localStorage.token, {
			model: modelId,
			messages: messages.map((m) => ({
				id: m.id,
				role: m.role,
				content: m.content,
				info: m.info ? m.info : undefined,
				timestamp: m.timestamp,
				...(m.usage ? { usage: m.usage } : {}),
				...(m.sources ? { sources: m.sources } : {})
			})),
			filter_ids: selectedFilterIds.length > 0 ? selectedFilterIds : undefined,
			model_item: $models.find((m) => m.id === modelId),
			chat_id: _chatId,
			...(selectedPersonaId
				? {
						persona_id: selectedPersonaId,
						...(sceneNote ? { scene_note: sceneNote } : {})
					}
				: {}),
			session_id: $socket?.id,
			id: responseMessageId
		}).catch((error) => {
			toast.error(`${error}`);
			messages.at(-1).error = { content: error };

			return null;
		});

		if (res !== null && res.messages) {
			// Update chat history with the new messages
			for (const message of res.messages) {
				if (message?.id) {
					// Add null check for message and message.id
					history.messages[message.id] = {
						...history.messages[message.id],
						...(history.messages[message.id].content !== message.content
							? { originalContent: history.messages[message.id].content }
							: {}),
						...message
					};
				}
			}
		}

		await tick();

		if ($chatId == _chatId) {
			if (!$temporaryChatEnabled) {
				chat = await updateChatById(
					localStorage.token,
					_chatId,
					{
						models: normalizeModelSelection(selectedModels),
						messages: messages,
						history: history,
						params: params,
						files: chatFiles
					},
					getPersonaMetaForPersistence(),
					selectedPersonaId
				);

				currentChatPage.set(1);
				await chats.set(await getChatList(localStorage.token, $currentChatPage));
			}
		}

		taskIds = null;

		// Process message queue - combine all queued messages and submit at once
		if (messageQueue.length > 0) {
			const combinedPrompt = messageQueue.map((m) => m.prompt).join('\n\n');
			const combinedFiles = messageQueue.flatMap((m) => m.files);
			messageQueue = [];

			// Set the files and submit
			files = combinedFiles;
			await tick();
			await submitPrompt(combinedPrompt);
		}
	};

	const chatActionHandler = async (_chatId, actionId, modelId, responseMessageId, event = null) => {
		const messages = createMessagesList(history, responseMessageId);

		const res = await chatAction(localStorage.token, actionId, {
			model: modelId,
			messages: messages.map((m) => ({
				id: m.id,
				role: m.role,
				content: m.content,
				info: m.info ? m.info : undefined,
				timestamp: m.timestamp,
				...(m.sources ? { sources: m.sources } : {})
			})),
			...(event ? { event: event } : {}),
			model_item: $models.find((m) => m.id === modelId),
			chat_id: _chatId,
			...(selectedPersonaId
				? {
						persona_id: selectedPersonaId,
						...(sceneNote ? { scene_note: sceneNote } : {})
					}
				: {}),
			session_id: $socket?.id,
			id: responseMessageId
		}).catch((error) => {
			toast.error(`${error}`);
			messages.at(-1).error = { content: error };
			return null;
		});

		if (res !== null && res.messages) {
			// Update chat history with the new messages
			for (const message of res.messages) {
				history.messages[message.id] = {
					...history.messages[message.id],
					...(history.messages[message.id].content !== message.content
						? { originalContent: history.messages[message.id].content }
						: {}),
					...message
				};
			}
		}

		if ($chatId == _chatId) {
			if (!$temporaryChatEnabled) {
				chat = await updateChatById(
					localStorage.token,
					_chatId,
					{
						models: normalizeModelSelection(selectedModels),
						messages: messages,
						history: history,
						params: params,
						files: chatFiles
					},
					getPersonaMetaForPersistence(),
					selectedPersonaId
				);

				currentChatPage.set(1);
				await chats.set(await getChatList(localStorage.token, $currentChatPage));
			}
		}
	};

	const getChatEventEmitter = async (modelId: string, chatId: string = '') => {
		return setInterval(() => {
			$socket?.emit('usage', {
				action: 'chat',
				model: modelId,
				chat_id: chatId
			});
		}, 1000);
	};

	const createMessagePair = async (userPrompt) => {
		messageInput?.setText('');
		if (selectedModels.length === 0) {
			toast.error($i18n.t('Model not selected'));
		} else {
			const modelId = selectedModels[0];
			const model = $models.filter((m) => m.id === modelId).at(0);

			if (!model) {
				toast.error($i18n.t('Model not found'));
				return;
			}

			const messages = createMessagesList(history, history.currentId);
			const parentMessage = messages.length !== 0 ? messages.at(-1) : null;

			const userMessageId = uuidv4();
			const responseMessageId = uuidv4();

			const userMessage = {
				id: userMessageId,
				parentId: parentMessage ? parentMessage.id : null,
				childrenIds: [responseMessageId],
				role: 'user',
				content: userPrompt ? userPrompt : `[PROMPT] ${userMessageId}`,
				timestamp: Math.floor(Date.now() / 1000)
			};

			const responseMessage = {
				id: responseMessageId,
				parentId: userMessageId,
				childrenIds: [],
				role: 'assistant',
				content: `[RESPONSE] ${responseMessageId}`,
				done: true,

				model: modelId,
				modelName: model.name ?? model.id,
				modelIdx: 0,
				timestamp: Math.floor(Date.now() / 1000)
			};

			if (parentMessage) {
				parentMessage.childrenIds.push(userMessageId);
				history.messages[parentMessage.id] = parentMessage;
			}
			history.messages[userMessageId] = userMessage;
			history.messages[responseMessageId] = responseMessage;

			history.currentId = responseMessageId;

			await tick();

			if (autoScroll) {
				scrollToBottom();
			}

			if (messages.length === 0) {
				await initChatHandler(history);
			} else {
				await saveChatHandler($chatId, history);
			}
		}
	};

	const addMessages = async ({ modelId, parentId, messages }) => {
		const model = $models.filter((m) => m.id === modelId).at(0);

		let parentMessage = history.messages[parentId];
		let currentParentId = parentMessage ? parentMessage.id : null;
		for (const message of messages) {
			let messageId = uuidv4();

			if (message.role === 'user') {
				const userMessage = {
					id: messageId,
					parentId: currentParentId,
					childrenIds: [],
					timestamp: Math.floor(Date.now() / 1000),
					...message
				};

				if (parentMessage) {
					parentMessage.childrenIds.push(messageId);
					history.messages[parentMessage.id] = parentMessage;
				}

				history.messages[messageId] = userMessage;
				parentMessage = userMessage;
				currentParentId = messageId;
			} else {
				const responseMessage = {
					id: messageId,
					parentId: currentParentId,
					childrenIds: [],
					done: true,
					model: model.id,
					modelName: model.name ?? model.id,
					modelIdx: 0,
					timestamp: Math.floor(Date.now() / 1000),
					...message
				};

				if (parentMessage) {
					parentMessage.childrenIds.push(messageId);
					history.messages[parentMessage.id] = parentMessage;
				}

				history.messages[messageId] = responseMessage;
				parentMessage = responseMessage;
				currentParentId = messageId;
			}
		}

		history.currentId = currentParentId;
		await tick();

		if (autoScroll) {
			scrollToBottom();
		}

		if (messages.length === 0) {
			await initChatHandler(history);
		} else {
			await saveChatHandler($chatId, history);
		}
	};

	const getTokenBranchDisplayPrefix = (message: Record<string, any> | null | undefined) => {
		const prefix = message?.tokenBranchDisplayPrefix ?? message?.tokenBranch?.displayPrefix;
		return typeof prefix === 'string' && prefix.length > 0 ? prefix : '';
	};

	const ensureTokenBranchDisplayPrefix = (message: Record<string, any>) => {
		const prefix = getTokenBranchDisplayPrefix(message);
		if (!prefix || typeof message?.content !== 'string' || message.content.startsWith(prefix)) {
			return;
		}

		message.content = `${prefix}${message.content}`;
	};

	const withTokenBranchDisplayPrefix = (message: Record<string, any>, content: unknown) => {
		const text = typeof content === 'string' ? content : '';
		const prefix = getTokenBranchDisplayPrefix(message);
		if (!prefix || text.startsWith(prefix)) {
			return text;
		}

		return `${prefix}${text}`;
	};

	const chatCompletionEventHandler = async (data, message, chatId) => {
		const {
			id,
			done,
			choices,
			content,
			output,
			sources,
			selected_model_id,
			error,
			usage,
			tokenTelemetry,
			tokenBranch,
			tokenTelemetryUnavailable,
			tokenTelemetryUnavailableReason
		} = data;

		applyCompletionTokenData(message, {
			tokenTelemetry,
			tokenBranch,
			tokenTelemetryUnavailable,
			tokenTelemetryUnavailableReason
		});

		// Store raw OR-aligned output items from backend
		if (output) {
			message.output = output;
		}

		if (error) {
			await handleOpenAIError(error, message);
		}

		if (sources && !message?.sources) {
			message.sources = sources;
		}

		if (choices) {
			if (choices[0]?.message?.content) {
				// Non-stream response
				ensureTokenBranchDisplayPrefix(message);
				message.content += choices[0]?.message?.content;
			} else {
				// Stream response
				let value = choices[0]?.delta?.content ?? '';
				ensureTokenBranchDisplayPrefix(message);
				if (message.content == '' && value == '\n') {
					console.log('Empty response');
				} else {
					message.content += value;

					if (navigator.vibrate && ($settings?.hapticFeedback ?? false)) {
						navigator.vibrate(5);
					}

					// Emit chat event for TTS (only when call overlay is active)
					if ($showCallOverlay) {
						const messageContentParts = getMessageContentParts(
							removeAllDetails(message.content),
							$config?.audio?.tts?.split_on ?? 'punctuation'
						);
						messageContentParts.pop();

						// dispatch only last sentence and make sure it hasn't been dispatched before
						if (
							messageContentParts.length > 0 &&
							messageContentParts[messageContentParts.length - 1] !== message.lastSentence
						) {
							message.lastSentence = messageContentParts[messageContentParts.length - 1];
							eventTarget.dispatchEvent(
								new CustomEvent('chat', {
									detail: {
										id: message.id,
										content: messageContentParts[messageContentParts.length - 1]
									}
								})
							);
						}
					}
				}
			}
		}

		if (content) {
			// REALTIME_CHAT_SAVE is disabled
			message.content = withTokenBranchDisplayPrefix(message, content);

			if (navigator.vibrate && ($settings?.hapticFeedback ?? false)) {
				navigator.vibrate(5);
			}

			// Emit chat event for TTS (only when call overlay is active)
			if ($showCallOverlay) {
				const messageContentParts = getMessageContentParts(
					removeAllDetails(message.content),
					$config?.audio?.tts?.split_on ?? 'punctuation'
				);
				messageContentParts.pop();

				// dispatch only last sentence and make sure it hasn't been dispatched before
				if (
					messageContentParts.length > 0 &&
					messageContentParts[messageContentParts.length - 1] !== message.lastSentence
				) {
					message.lastSentence = messageContentParts[messageContentParts.length - 1];
					eventTarget.dispatchEvent(
						new CustomEvent('chat', {
							detail: {
								id: message.id,
								content: messageContentParts[messageContentParts.length - 1]
							}
						})
					);
				}
			}
		}

		if (selected_model_id) {
			message.selectedModelId = selected_model_id;
			message.arena = true;
		}

		if (usage) {
			message.usage = usage;
		}

		history.messages[message.id] = message;

		if (done) {
			message.done = true;

			if ($settings.responseAutoCopy) {
				copyToClipboard(message.content);
			}

			if ($settings.responseAutoPlayback && !$showCallOverlay) {
				await tick();
				document.getElementById(`speak-button-${message.id}`)?.click();
			}

			// Emit chat event for TTS (only when call overlay is active)
			if ($showCallOverlay) {
				let lastMessageContentPart =
					getMessageContentParts(
						removeAllDetails(message.content),
						$config?.audio?.tts?.split_on ?? 'punctuation'
					)?.at(-1) ?? '';
				if (lastMessageContentPart) {
					eventTarget.dispatchEvent(
						new CustomEvent('chat', {
							detail: { id: message.id, content: lastMessageContentPart }
						})
					);
				}
			}
			eventTarget.dispatchEvent(
				new CustomEvent('chat:finish', {
					detail: {
						id: message.id,
						content: message.content
					}
				})
			);

			history.messages[message.id] = message;

			await tick();
			if (autoScroll) {
				scrollToBottom();
			}

			await chatCompletedHandler(
				chatId,
				message.model,
				message.id,
				createMessagesList(history, message.id)
			);

			if (message.id === history.currentId) {
				scheduleContextWindowPreviewRefresh(0, true);
			}
		}

		console.log(data);
		await tick();

		if (autoScroll) {
			scheduleScrollToBottom();
		}
	};

	//////////////////////////
	// Chat functions
	//////////////////////////

	const submitPrompt = async (userPrompt, { _raw = false } = {}) => {
		console.log('submitPrompt', userPrompt, $chatId);

		const _selectedModels = selectedModels.map((modelId) =>
			$models.map((m) => m.id).includes(modelId) ? modelId : ''
		);

		if (JSON.stringify(selectedModels) !== JSON.stringify(_selectedModels)) {
			selectedModels = _selectedModels;
		}

		if (userPrompt === '' && files.length === 0) {
			toast.error($i18n.t('Please enter a prompt'));
			return;
		}
		if (selectedModels.includes('')) {
			toast.error($i18n.t('Model not selected'));
			return;
		}

		if (
			files.length > 0 &&
			files.filter((file) => file.type !== 'image' && file.status === 'uploading').length > 0
		) {
			toast.error(
				$i18n.t(`Oops! There are files still uploading. Please wait for the upload to complete.`)
			);
			return;
		}

		if (
			($config?.file?.max_count ?? null) !== null &&
			files.length + chatFiles.length > $config?.file?.max_count
		) {
			toast.error(
				$i18n.t(`You can only chat with a maximum of {{maxCount}} file(s) at a time.`, {
					maxCount: $config?.file?.max_count
				})
			);
			return;
		}

		// Check if there are pending tasks (more reliable than lastMessage.done)
		if (taskIds !== null && taskIds.length > 0) {
			if ($settings?.enableMessageQueue ?? true) {
				// Queue the message
				const _files = structuredClone(files);
				messageQueue = [
					...messageQueue,
					{
						id: uuidv4(),
						prompt: userPrompt,
						files: _files
					}
				];
				// Clear input
				messageInput?.setText('');
				prompt = '';
				files = [];
				return;
			} else {
				// Interrupt: stop current generation and proceed
				await stopResponse();
				await tick();
			}
		}

		if (history?.currentId) {
			const lastMessage = history.messages[history.currentId];

			if (lastMessage.error && !lastMessage.content) {
				// Keep failed assistant turn visible, but allow the user to continue with a new turn.
				lastMessage.done = true;
				history.messages[history.currentId] = lastMessage;
			}
		}

		messageInput?.setText('');
		prompt = '';

		const messages = createMessagesList(history, history.currentId);
		const _files = structuredClone(files);

		chatFiles.push(
			..._files.filter(
				(item) =>
					['doc', 'text', 'note', 'chat', 'folder', 'collection'].includes(item.type) ||
					(item.type === 'file' && !(item?.content_type ?? '').startsWith('image/'))
			)
		);
		chatFiles = chatFiles.filter(
			// Remove duplicates
			(item, index, array) =>
				array.findIndex((i) => JSON.stringify(i) === JSON.stringify(item)) === index
		);

		files = [];
		messageInput?.setText('');

		// Create user message
		let userMessageId = uuidv4();
		let userMessage = {
			id: userMessageId,
			parentId: messages.length !== 0 ? messages.at(-1).id : null,
			childrenIds: [],
			role: 'user',
			content: userPrompt,
			files: _files.length > 0 ? _files : undefined,
			timestamp: Math.floor(Date.now() / 1000), // Unix epoch
			models: normalizeModelSelection(selectedModels)
		};

		// Add message to history and Set currentId to messageId
		history.messages[userMessageId] = userMessage;
		history.currentId = userMessageId;

		// Append messageId to childrenIds of parent message
		if (messages.length !== 0) {
			history.messages[messages.at(-1).id].childrenIds.push(userMessageId);
		}

		// focus on chat input
		const chatInput = document.getElementById('chat-input');
		chatInput?.focus();

		saveSessionSelectedModels();

		await sendMessage(history, userMessageId, { newChat: true });
	};

	const sendMessage = async (
		_history,
		parentId: string,
		{
			messages = null,
			modelId = null,
			modelIdx = null,
			newChat = false,
			branch = null,
			branchDisplayPrefix = null
		}: {
			messages?: any[] | null;
			modelId?: string | null;
			modelIdx?: number | null;
			newChat?: boolean;
			branch?: TokenBranchRequest | null;
			branchDisplayPrefix?: string | null;
		} = {}
	) => {
		if (autoScroll) {
			scrollToBottom();
		}

		let _chatId = JSON.parse(JSON.stringify($chatId));
		_history = structuredClone(_history);

		const responseMessageIds: Record<PropertyKey, string> = {};
		// If modelId is provided, use it, else use selected model
		let selectedModelIds = modelId
			? [modelId]
			: atSelectedModel !== undefined
				? [atSelectedModel.id]
				: selectedModels;

		// Create response messages for each selected model
		for (const [_modelIdx, modelId] of selectedModelIds.entries()) {
			const model = $models.filter((m) => m.id === modelId).at(0);

			if (model) {
				let responseMessageId = uuidv4();
				let responseMessage = {
					parentId: parentId,
					id: responseMessageId,
					childrenIds: [],
					role: 'assistant',
					content: '',
					model: model.id,
					modelName: model.name ?? model.id,
					modelIdx: modelIdx ? modelIdx : _modelIdx,
					timestamp: Math.floor(Date.now() / 1000), // Unix epoch
					...(branchDisplayPrefix ? { tokenBranchDisplayPrefix: branchDisplayPrefix } : {})
				};

				// Add message to history and Set currentId to messageId
				history.messages[responseMessageId] = responseMessage;
				history.currentId = responseMessageId;

				// Append messageId to childrenIds of parent message
				if (parentId !== null && history.messages[parentId]) {
					// Add null check before accessing childrenIds
					history.messages[parentId].childrenIds = [
						...history.messages[parentId].childrenIds,
						responseMessageId
					];
				}

				responseMessageIds[`${modelId}-${modelIdx ? modelIdx : _modelIdx}`] = responseMessageId;
			}
		}
		history = history;

		// Create new chat if newChat is true and first user message
		if (newChat && _history.messages[_history.currentId].parentId === null) {
			_chatId = await initChatHandler(_history);
		}

		await tick();

		_history = structuredClone(history);
		// Save chat after all messages have been created
		await saveChatHandler(_chatId, _history);

		await Promise.all(
			selectedModelIds.map(async (modelId, _modelIdx) => {
				console.log('modelId', modelId);
				const model = $models.filter((m) => m.id === modelId).at(0);

				if (model) {
					// If there are image files, check if model is vision capable
					// Skip this check if image generation is enabled, as images may be for editing or are generated outputs in the history
					const hasImages = createMessagesList(_history, parentId).some((message) =>
						message.files?.some(
							(file) => file.type === 'image' || (file?.content_type ?? '').startsWith('image/')
						)
					);

					if (
						hasImages &&
						!(model.info?.meta?.capabilities?.vision ?? true) &&
						!imageGenerationEnabled
					) {
						toast.error(
							$i18n.t('Model {{modelName}} is not vision capable', {
								modelName: model.name ?? model.id
							})
						);
					}

					let responseMessageId =
						responseMessageIds[`${modelId}-${modelIdx ? modelIdx : _modelIdx}`];
					const chatEventEmitter = await getChatEventEmitter(model.id, _chatId);

					scrollToBottom();
					await sendMessageSocket(
						model,
						messages && messages.length > 0
							? messages
							: createMessagesList(_history, responseMessageId),
						_history,
						responseMessageId,
						_chatId,
						branch
					);

					if (chatEventEmitter) clearInterval(chatEventEmitter);
				} else {
					toast.error($i18n.t(`Model {{modelId}} not found`, { modelId }));
				}
			})
		);
	};

	const getFeatures = () => {
		let features = {};
		const currentModels = atSelectedModel?.id ? [atSelectedModel.id] : selectedModels;
		const webSearchAllowedByRole =
			$config?.features?.enable_web_search &&
			($user?.role === 'admin' || $user?.permissions?.features?.web_search);
		const allModelsWebSearchCapable =
			currentModels.filter(
				(model) => $models.find((m) => m.id === model)?.info?.meta?.capabilities?.web_search ?? true
			).length === currentModels.length;

		if ($config?.features)
			features = {
				voice: $showCallOverlay,
				image_generation:
					$config?.features?.enable_image_generation &&
					($user?.role === 'admin' || $user?.permissions?.features?.image_generation)
						? imageGenerationEnabled
						: false,
				code_interpreter:
					$config?.features?.enable_code_interpreter &&
					($user?.role === 'admin' || $user?.permissions?.features?.code_interpreter)
						? codeInterpreterEnabled
						: false,
				web_search: webSearchAllowedByRole ? webSearchEnabled : false,
				focused_search:
					webSearchAllowedByRole && allModelsWebSearchCapable ? chatFocusedSearchEnabled : false
			};

		if (allModelsWebSearchCapable) {
			if ($config?.features?.enable_web_search && ($settings?.webSearch ?? false) === 'always') {
				features = { ...features, web_search: true };
			}
		}

		if ($settings?.memory ?? false) {
			features = { ...features, memory: true };
		}

		return features;
	};

	const getStopTokens = () => {
		const stop = params?.stop ?? $settings?.params?.stop;
		if (!stop) return undefined;

		const tokens = Array.isArray(stop) ? stop : stop.split(',').map((s) => s.trim());

		return tokens
			.filter(Boolean)
			.map((token) => decodeURIComponent(JSON.parse(`"${token.replace(/"/g, '\\"')}"`)));
	};

	const sendMessageSocket = async (
		model,
		_messages,
		_history,
		responseMessageId,
		_chatId,
		branch: TokenBranchRequest | null = null
	) => {
		const responseMessage = _history.messages[responseMessageId];
		const userMessage = _history.messages[responseMessage.parentId];

		const chatMessageFiles = _messages
			.filter((message) => message.files)
			.flatMap((message) => message.files);

		// Filter chatFiles to only include files that are in the chatMessageFiles
		chatFiles = chatFiles.filter((item) => {
			const fileExists = chatMessageFiles.some((messageFile) => messageFile.id === item.id);
			return fileExists;
		});

		let files = structuredClone(chatFiles);
		files.push(
			...(userMessage?.files ?? []).filter(
				(item) =>
					['doc', 'text', 'note', 'chat', 'collection'].includes(item.type) ||
					(item.type === 'file' && !(item?.content_type ?? '').startsWith('image/'))
			)
		);
		// Remove duplicates
		files = files.filter(
			(item, index, array) =>
				array.findIndex((i) => JSON.stringify(i) === JSON.stringify(item)) === index
		);

		scrollToBottom();
		eventTarget.dispatchEvent(
			new CustomEvent('chat:start', {
				detail: {
					id: responseMessageId
				}
			})
		);
		await tick();

		let userLocation;
		if ($settings?.userLocation) {
			userLocation = await getAndUpdateUserLocation(localStorage.token).catch((err) => {
				console.error(err);
				return undefined;
			});
		}

		const stream =
			model?.info?.params?.stream_response ??
			$settings?.params?.stream_response ??
			params?.stream_response ??
			true;
		const systemMessageContent = hasOwn(params, 'system')
			? params.system
			: ($settings?.system ?? undefined);

		let messages = [
			systemMessageContent !== undefined && systemMessageContent !== null
				? {
						role: 'system',
						content: `${systemMessageContent ?? ''}`
					}
				: undefined,
			..._messages.map((message) => ({
				...message,
				content: processDetails(message.content),
				// Include output for temp chats (backend will use it and strip before LLM)
				...(message.output ? { output: message.output } : {})
			}))
		].filter((message) => message);

		messages = messages
			.map((message, idx, arr) => {
				const imageFiles = (message?.files ?? []).filter(
					(file) => file.type === 'image' || (file?.content_type ?? '').startsWith('image/')
				);

				return {
					role: message.role,
					...(message.role === 'user' && imageFiles.length > 0
						? {
								content: [
									{
										type: 'text',
										text: message?.merged?.content ?? message.content
									},
									...imageFiles.map((file) => ({
										type: 'image_url',
										image_url: {
											url: file.url
										}
									}))
								]
							}
						: {
								content: message?.merged?.content ?? message.content
							})
				};
			})
			.filter(
				(message) =>
					message?.role === 'system' || message?.role === 'user' || message?.content?.trim()
			);

		const toolIds = [];
		const toolServerIds = [];

		for (const toolId of selectedToolIds) {
			if (toolId.startsWith('direct_server:')) {
				let serverId = toolId.replace('direct_server:', '');
				// Check if serverId is a number
				if (!isNaN(parseInt(serverId))) {
					toolServerIds.push(parseInt(serverId));
				} else {
					toolServerIds.push(serverId);
				}
			} else {
				toolIds.push(toolId);
			}
		}

		// Parse skill mentions (<$skillId|label>) from user messages
		const skillMentionRegex = /<\$([^|>]+)\|?[^>]*>/g;
		const skillIds = [];
		for (const message of messages) {
			const content =
				typeof message.content === 'string' ? message.content : (message.content?.[0]?.text ?? '');
			for (const match of content.matchAll(skillMentionRegex)) {
				if (!skillIds.includes(match[1])) {
					skillIds.push(match[1]);
				}
			}
		}

		// Strip skill mentions from message content
		if (skillIds.length > 0) {
			messages = messages.map((message) => {
				if (typeof message.content === 'string') {
					return {
						...message,
						content: message.content.replace(/<\$[^>]+>/g, '').trim()
					};
				} else if (Array.isArray(message.content)) {
					return {
						...message,
						content: message.content.map((part) =>
							part.type === 'text'
								? { ...part, text: part.text.replace(/<\$[^>]+>/g, '').trim() }
								: part
						)
					};
				}
				return message;
			});
		}

		const activeTerminalId = $selectedTerminalId ?? null;
		let requestParams = {
			...$settings?.params,
			...params,
			stop: getStopTokens()
		};

		requestParams = applyTokenExplorerDefaults(
			requestParams,
			$settings?.tokenExplorerEnabled ?? false
		);

		const personaSnapshot = selectedPersona ? getCurrentPersonaSnapshot() : null;
		const personaOverrides = selectedPersona ? getCurrentPersonaOverrides() : {};

		const res = await generateOpenAIChatCompletion(
			localStorage.token,
			{
				stream: stream,
				model: model.id,
				messages: messages,
				params: requestParams,
				files: (files?.length ?? 0) > 0 ? files : undefined,
				filter_ids: selectedFilterIds.length > 0 ? selectedFilterIds : undefined,
				tool_ids: toolIds.length > 0 ? toolIds : undefined,
				skill_ids: skillIds.length > 0 ? skillIds : undefined,
				terminal_id: activeTerminalId ?? undefined,
				tool_servers: [
					...($toolServers ?? []).filter(
						(server, idx) => toolServerIds.includes(idx) || toolServerIds.includes(server?.id)
					),
					...($terminalServers ?? []).filter((t) => !t.id)
				],
				features: getFeatures(),
				variables: {
					...getPromptVariables(
						$user?.name,
						$settings?.userLocation ? userLocation : undefined,
						$user?.email
					)
				},
				model_item: $models.find((m) => m.id === model.id),
				session_id: $socket?.id,
				chat_id: $chatId,
				...(selectedPersonaId
					? {
							persona_id: selectedPersonaId,
							persona_defaults_snapshot: personaSnapshot,
							persona_chat_overrides: personaOverrides,
							...(sceneNote ? { scene_note: sceneNote } : {})
						}
					: {}),
				id: responseMessageId,
				parent_id: userMessage?.id ?? null,
				parent_message: userMessage,
				...(branch ? { branch } : {}),
				background_tasks: {
					...(!$temporaryChatEnabled &&
					(messages.length == 1 ||
						(messages.length == 2 &&
							messages.at(0)?.role === 'system' &&
							messages.at(1)?.role === 'user')) &&
					(selectedModels[0] === model.id || atSelectedModel !== undefined)
						? {
								title_generation: $settings?.title?.auto ?? true,
								tags_generation: $settings?.autoTags ?? true
							}
						: {}),
					follow_up_generation: $settings?.autoFollowUps ?? false,
					context_maintenance:
						$settings?.contextMaintenance ?? $config?.features?.enable_context_maintenance ?? true
				},
				...(stream && (model.info?.meta?.capabilities?.usage ?? false)
					? {
							stream_options: {
								include_usage: true
							}
						}
					: {})
			},
			`${WEBUI_BASE_URL}/api`
		).catch(async (error) => {
			console.log(error);

			let errorMessage = error;
			if (error?.error?.message) {
				errorMessage = error.error.message;
			} else if (error?.message) {
				errorMessage = error.message;
			}

			if (typeof errorMessage === 'object') {
				errorMessage = $i18n.t(`Uh-oh! There was an issue with the response.`);
			}

			toast.error(`${errorMessage}`);
			responseMessage.error = {
				content: error
			};

			responseMessage.done = true;

			history.messages[responseMessageId] = responseMessage;
			history.currentId = responseMessageId;

			return null;
		});

		if (res) {
			if (res.error) {
				await handleOpenAIError(res.error, responseMessage);
			} else {
				if (taskIds) {
					taskIds.push(res.task_id);
				} else {
					taskIds = [res.task_id];
				}
			}
		}

		await tick();
		scrollToBottom();
	};

	const handleOpenAIError = async (error, responseMessage) => {
		let errorMessage = '';
		let innerError;

		if (error) {
			innerError = error;
		}

		console.error(innerError);
		if ('detail' in innerError) {
			// FastAPI error
			toast.error(innerError.detail);
			errorMessage = innerError.detail;
		} else if ('error' in innerError) {
			// OpenAI error
			if ('message' in innerError.error) {
				toast.error(innerError.error.message);
				errorMessage = innerError.error.message;
			} else {
				toast.error(innerError.error);
				errorMessage = innerError.error;
			}
		} else if ('message' in innerError) {
			// OpenAI error
			toast.error(innerError.message);
			errorMessage = innerError.message;
		}

		responseMessage.error = {
			content: $i18n.t(`Uh-oh! There was an issue with the response.`) + '\n' + errorMessage
		};
		responseMessage.done = true;

		if (responseMessage.statusHistory) {
			responseMessage.statusHistory = responseMessage.statusHistory.filter(
				(status) => status.action !== 'knowledge_search'
			);
		}

		history.messages[responseMessage.id] = responseMessage;
	};

	const stopResponse = async () => {
		if (taskIds) {
			for (const taskId of taskIds) {
				const res = await stopTask(localStorage.token, taskId).catch((error) => {
					toast.error(`${error}`);
					return null;
				});
			}

			taskIds = null;

			const responseMessage = history.messages[history.currentId];
			// Set all response messages to done
			if (responseMessage.parentId && history.messages[responseMessage.parentId]) {
				for (const messageId of history.messages[responseMessage.parentId].childrenIds) {
					history.messages[messageId].done = true;
				}
			}

			history.messages[history.currentId] = responseMessage;

			if (autoScroll) {
				scrollToBottom();
			}
		}

		if (generating) {
			generating = false;
			generationController?.abort();
			generationController = null;
		}
	};

	const submitMessage = async (parentId, prompt) => {
		let userPrompt = prompt;
		let userMessageId = uuidv4();

		let userMessage = {
			id: userMessageId,
			parentId: parentId,
			childrenIds: [],
			role: 'user',
			content: userPrompt,
			models: normalizeModelSelection(selectedModels),
			timestamp: Math.floor(Date.now() / 1000) // Unix epoch
		};

		if (parentId !== null) {
			history.messages[parentId].childrenIds = [
				...history.messages[parentId].childrenIds,
				userMessageId
			];
		}

		history.messages[userMessageId] = userMessage;
		history.currentId = userMessageId;

		await tick();

		if (autoScroll) {
			scrollToBottom();
		}

		await sendMessage(history, userMessageId);
	};

	const regenerateResponse = async (message, suggestionPrompt = null) => {
		console.log('regenerateResponse');

		if (history.currentId) {
			let userMessage = history.messages[message.parentId];

			if (!userMessage) {
				toast.error($i18n.t('Parent message not found'));
				return;
			}

			if (autoScroll) {
				scrollToBottom();
			}

			await sendMessage(history, userMessage.id, {
				...(suggestionPrompt
					? {
							messages: [
								...createMessagesList(history, message.id),
								{
									role: 'user',
									content: suggestionPrompt
								}
							]
						}
					: {}),
				...((userMessage?.models ?? [...selectedModels]).length > 1
					? {
							// If multiple models are selected, use the model from the message
							modelId: message.model,
							modelIdx: message.modelIdx
						}
					: {})
			});
		}
	};

	const continueResponse = async () => {
		console.log('continueResponse');
		const _chatId = JSON.parse(JSON.stringify($chatId));

		if (history.currentId && history.messages[history.currentId].done == true) {
			const responseMessage = history.messages[history.currentId];
			responseMessage.done = false;
			await tick();

			const model = $models
				.filter((m) => m.id === (responseMessage?.selectedModelId ?? responseMessage.model))
				.at(0);

			if (model) {
				await sendMessageSocket(
					model,
					createMessagesList(history, responseMessage.id),
					history,
					responseMessage.id,
					_chatId
				);
			}
		}
	};

	const createTokenBranch = async (sourceMessage, forkIndex: number, altRank: number) => {
		if (!sourceMessage?.id || sourceMessage?.role !== 'assistant') {
			toast.error($i18n.t('Invalid branch source message'));
			return;
		}

		if (!sourceMessage?.parentId) {
			toast.error($i18n.t('Parent message not found'));
			return;
		}

		const branchDisplayPrefix = buildTokenBranchDisplayPrefix(
			sourceMessage.tokenTelemetry,
			forkIndex,
			altRank
		);

		await sendMessage(history, sourceMessage.parentId, {
			modelId: sourceMessage.model,
			modelIdx: sourceMessage.modelIdx,
			branch: buildTokenBranchPayload(sourceMessage.id, forkIndex, altRank),
			branchDisplayPrefix
		});
	};

	const mergeResponses = async (messageId, responses, _chatId) => {
		console.log('mergeResponses', messageId, responses);
		const message = history.messages[messageId];
		const mergedResponse = {
			status: true,
			content: ''
		};
		message.merged = mergedResponse;
		history.messages[messageId] = message;

		try {
			generating = true;
			const [res, controller] = await generateMoACompletion(
				localStorage.token,
				message.model ?? '',
				message.parentId ? history.messages[message.parentId].content : '',
				responses
			);

			if (res && res.ok && res.body && generating) {
				generationController = controller as AbortController;
				const textStream = await createOpenAITextStream(
					res.body,
					Boolean($settings?.splitLargeChunks ?? false)
				);
				for await (const update of textStream) {
					const { value, done, sources, error, usage } = update;
					if (error || done) {
						generating = false;
						generationController = null;
						break;
					}

					if (mergedResponse.content == '' && value == '\n') {
						continue;
					} else {
						mergedResponse.content += value;
						history.messages[messageId] = message;
					}

					if (autoScroll) {
						scheduleScrollToBottom();
					}
				}

				await saveChatHandler(_chatId, history);
			} else {
				console.error(res);
			}
		} catch (e) {
			console.error(e);
		}
	};

	const initChatHandler = async (history) => {
		let _chatId = $chatId;
		const personaMeta = getPersonaMetaForPersistence();

		if (!$temporaryChatEnabled) {
			chat = await createNewChat(
				localStorage.token,
				{
					id: _chatId,
					title: $i18n.t('New Chat'),
					models: normalizeModelSelection(selectedModels),
					system: $settings.system ?? undefined,
					params: params,
					history: history,
					messages: createMessagesList(history, history.currentId),
					tags: [],
					timestamp: Date.now()
				},
				$selectedFolder?.id,
				personaMeta,
				selectedPersonaId
			);

			_chatId = chat.id;
			await chatId.set(_chatId);

			window.history.replaceState(history.state, '', `/c/${_chatId}`);

			await tick();

			await chats.set(await getChatList(localStorage.token, $currentChatPage));
			currentChatPage.set(1);

			selectedFolder.set(null);
		} else {
			_chatId = `local:${$socket?.id}`; // Use socket id for temporary chat
			await chatId.set(_chatId);
		}
		await tick();

		return _chatId;
	};

	const saveChatHandler = async (_chatId, history) => {
		if ($chatId == _chatId) {
			if (!$temporaryChatEnabled) {
				chat = await updateChatById(
					localStorage.token,
					_chatId,
					{
						models: normalizeModelSelection(selectedModels),
						history: history,
						messages: createMessagesList(history, history.currentId),
						params: params,
						files: chatFiles
					},
					getPersonaMetaForPersistence(),
					selectedPersonaId
				);
				currentChatPage.set(1);
				await chats.set(await getChatList(localStorage.token, $currentChatPage));
			}
		}
	};

	const MAX_DRAFT_LENGTH = 5000;
	let saveDraftTimeout: ReturnType<typeof setTimeout> | null = null;
	let latestDraft: Record<string, unknown> | null = null;
	let latestDraftChatId: string | null = null;

	const getDraftStorageKey = (chatId: string | null = null) =>
		`chat-input${chatId ? `-${chatId}` : ''}`;

	const isDraftPersistable = (draft: Record<string, unknown> | null | undefined) =>
		typeof draft?.prompt === 'string' && draft.prompt.length < MAX_DRAFT_LENGTH;

	const persistDraftNow = (draft: Record<string, unknown>, chatId: string | null = null) => {
		sessionStorage.setItem(getDraftStorageKey(chatId), JSON.stringify(draft));
	};

	const flushPendingDraft = () => {
		if (saveDraftTimeout) {
			clearTimeout(saveDraftTimeout);
			saveDraftTimeout = null;
		}

		if (latestDraft && isDraftPersistable(latestDraft)) {
			persistDraftNow(latestDraft, latestDraftChatId);
		}
	};

	const saveDraft = async (draft, chatId = null) => {
		latestDraft = draft;
		latestDraftChatId = chatId;

		if (saveDraftTimeout) {
			clearTimeout(saveDraftTimeout);
		}

		if (isDraftPersistable(draft)) {
			saveDraftTimeout = setTimeout(async () => {
				persistDraftNow(draft, chatId);
				saveDraftTimeout = null;
			}, 500);
		} else {
			latestDraft = null;
			latestDraftChatId = null;
			sessionStorage.removeItem(getDraftStorageKey(chatId));
		}
	};

	const clearDraft = async (chatId = null) => {
		if (saveDraftTimeout) {
			clearTimeout(saveDraftTimeout);
			saveDraftTimeout = null;
		}
		latestDraft = null;
		latestDraftChatId = null;
		await sessionStorage.removeItem(getDraftStorageKey(chatId));
	};

	beforeNavigate(() => {
		flushPendingDraft();
	});

	onDestroy(() => {
		flushPendingDraft();
	});

	const moveChatHandler = async (chatId, folderId) => {
		if (chatId && folderId) {
			const res = await updateChatFolderIdById(localStorage.token, chatId, folderId).catch(
				(error) => {
					toast.error(`${error}`);
					return null;
				}
			);

			if (res) {
				currentChatPage.set(1);
				await chats.set(await getChatList(localStorage.token, $currentChatPage));
				await pinnedChats.set(await getPinnedChatList(localStorage.token));

				toast.success($i18n.t('Chat moved successfully'));
			}
		} else {
			toast.error($i18n.t('Failed to move chat'));
		}
	};

	const archiveChatHandler = async (id: string) => {
		try {
			await archiveChatById(localStorage.token, id);
			currentChatPage.set(1);
			initNewChat();
			await goto('/');
			getChatList(localStorage.token, $currentChatPage).then((chats) => {
				chats.set(chats);
			});
			getPinnedChatList(localStorage.token).then((pinnedChats) => {
				pinnedChats.set(pinnedChats);
			});
			toast.success($i18n.t('Chat archived.'));
		} catch (error) {
			console.error('Error archiving chat:', error);
			toast.error($i18n.t('Failed to archive chat.'));
		}
	};
</script>

<svelte:head>
	<title>
		{$settings.showChatTitleInTab !== false && $chatTitle
			? `${$chatTitle.length > 30 ? `${$chatTitle.slice(0, 30)}...` : $chatTitle} • ${$WEBUI_NAME}`
			: `${$WEBUI_NAME}`}
	</title>
</svelte:head>

<audio id="audioElement" src="" style="display: none;"></audio>

<EventConfirmDialog
	bind:show={showEventConfirmation}
	title={eventConfirmationTitle}
	message={eventConfirmationMessage}
	input={eventConfirmationInput}
	inputPlaceholder={eventConfirmationInputPlaceholder}
	inputValue={eventConfirmationInputValue}
	inputType={eventConfirmationInputType}
	on:confirm={(e) => {
		if (e.detail) {
			eventCallback(e.detail);
		} else {
			eventCallback(true);
		}
	}}
	on:cancel={() => {
		eventCallback(false);
	}}
/>

<SceneNoteModal
	bind:show={showSceneNoteModal}
	value={sceneNote}
	on:save={(event) => {
		persistSceneNote(event.detail);
	}}
/>

<div
	class="h-screen max-h-[100dvh] transition-width duration-200 ease-in-out {$showSidebar
		? '  md:max-w-[calc(100%-var(--sidebar-width))]'
		: ' '} w-full max-w-full flex flex-col"
	id="chat-container"
>
	{#if !loading}
		<div in:fade={{ duration: 50 }} class="w-full h-full flex flex-col">
			{#if $selectedFolder && $selectedFolder?.meta?.background_image_url}
				<div
					class="absolute top-0 left-0 w-full h-full bg-cover bg-center bg-no-repeat"
					style="background-image: url({$selectedFolder?.meta?.background_image_url})  "
				/>

				<div
					class="absolute top-0 left-0 w-full h-full bg-linear-to-t from-white to-white/85 dark:from-gray-900 dark:to-gray-900/90 z-0"
				/>
			{:else if $settings?.backgroundImageUrl ?? $config?.license_metadata?.background_image_url ?? null}
				<div
					class="absolute top-0 left-0 w-full h-full bg-cover bg-center bg-no-repeat"
					style="background-image: url({$settings?.backgroundImageUrl ??
						$config?.license_metadata?.background_image_url})  "
				/>

				<div
					class="absolute top-0 left-0 w-full h-full bg-linear-to-t from-white to-white/85 dark:from-gray-900 dark:to-gray-900/90 z-0"
				/>
			{/if}

			<PaneGroup direction="horizontal" class="w-full h-full">
				<Pane defaultSize={50} minSize={30} class="h-full flex relative max-w-full flex-col">
					<FilesOverlay show={dragged} />
					<Navbar
						bind:this={navbarElement}
						chat={{
							id: $chatId,
							persona_id: selectedPersonaId,
							meta: getPersonaMetaForPersistence(),
							chat: {
								title: $chatTitle,
								models: normalizeModelSelection(selectedModels),
								system: $settings.system ?? undefined,
								params: params,
								history: history,
								timestamp: Date.now()
							}
						}}
						{history}
						{contextWindowPreview}
						{contextWindowRuntimeState}
						draftPrompt={prompt}
						title={$chatTitle}
						bind:selectedModels
						{selectedPersonaId}
						onPersonaSelect={handlePersonaSelect}
						{activeChatIdentity}
						sceneNoteLabel={activeSceneNoteLabel}
						onEditSceneNote={() => {
							showSceneNoteModal = true;
						}}
						shareEnabled={!!history.currentId}
						{initNewChat}
						{archiveChatHandler}
						{moveChatHandler}
						onSaveTempChat={async () => {
							try {
								if (!history?.currentId || !Object.keys(history.messages).length) {
									toast.error($i18n.t('No conversation to save'));
									return;
								}
								const messages = createMessagesList(history, history.currentId);
								const title =
									messages.find((m) => m.role === 'user')?.content ?? $i18n.t('New Chat');

								const savedChat = await createNewChat(
									localStorage.token,
									{
										id: uuidv4(),
										title: title.length > 50 ? `${title.slice(0, 50)}...` : title,
										models: normalizeModelSelection(selectedModels),
										params: params,
										history: history,
										messages: messages,
										timestamp: Date.now()
									},
									null,
									getPersonaMetaForPersistence(),
									selectedPersonaId
								);

								if (savedChat) {
									temporaryChatEnabled.set(false);
									chatId.set(savedChat.id);
									chats.set(await getChatList(localStorage.token, $currentChatPage));

									await goto(`/c/${savedChat.id}`);
									toast.success($i18n.t('Conversation saved successfully'));
								}
							} catch (error) {
								console.error('Error saving conversation:', error);
								toast.error($i18n.t('Failed to save conversation'));
							}
						}}
					/>

					<div id="chat-pane" class="flex flex-col flex-auto z-10 w-full @container overflow-auto">
						{#if ($settings?.landingPageMode === 'chat' && !$selectedFolder) || createMessagesList(history, history.currentId).length > 0}
							<div
								class=" pb-2.5 flex flex-col justify-between w-full flex-auto overflow-auto h-0 max-w-full z-10 scrollbar-hidden"
								id="messages-container"
								bind:this={messagesContainerElement}
								on:scroll={(e) => {
									autoScroll =
										messagesContainerElement.scrollHeight - messagesContainerElement.scrollTop <=
										messagesContainerElement.clientHeight + 5;
								}}
							>
								<div class=" h-full w-full flex flex-col">
									<Messages
										chatId={$chatId}
										bind:history
										bind:autoScroll
										bind:prompt
										setInputText={(text) => {
											messageInput?.setText(text);
										}}
										{selectedModels}
										{atSelectedModel}
										voicePreference={activeVoicePreference}
										{sendMessage}
										{showMessage}
										{submitMessage}
										{createTokenBranch}
										{continueResponse}
										{regenerateResponse}
										{mergeResponses}
										{chatActionHandler}
										{addMessages}
										topPadding={true}
										bottomPadding={files.length > 0}
										{onSelect}
									/>
								</div>
							</div>

							<div class=" pb-2 z-10">
								<MessageInput
									bind:this={messageInput}
									{history}
									{taskIds}
									{selectedModels}
									thinkingEnabled={chatThinkingEnabled}
									{setChatThinkingEnabled}
									ledgerAgenticEnabled={chatLedgerAgenticEnabled}
									{setChatLedgerAgenticEnabled}
									focusedSearchEnabled={chatFocusedSearchEnabled}
									{setChatFocusedSearchEnabled}
									workingMode={chatWorkingMode}
									{setChatWorkingMode}
									localCorpusMode={chatLocalCorpusMode}
									{setChatLocalCorpusMode}
									scienceResearchMode={chatScienceResearchMode}
									{setChatScienceResearchMode}
									scienceAttachedCorpora={chatScienceAttachedCorpora}
									{setChatScienceAttachedCorpora}
									bind:files
									bind:prompt
									bind:autoScroll
									bind:selectedToolIds
									bind:selectedFilterIds
									bind:imageGenerationEnabled
									bind:codeInterpreterEnabled
									bind:webSearchEnabled
									bind:atSelectedModel
									bind:showCommands
									bind:dragged
									toolServers={$toolServers}
									{generating}
									{stopResponse}
									{createMessagePair}
									{onUpload}
									{messageQueue}
									onQueueSendNow={async (id) => {
										const item = messageQueue.find((m) => m.id === id);
										if (item) {
											// Remove from queue
											messageQueue = messageQueue.filter((m) => m.id !== id);
											// Stop current generation first
											await stopResponse();
											await tick();
											// Set files and submit
											files = item.files;
											await tick();
											await submitPrompt(item.prompt);
										}
									}}
									onQueueEdit={(id) => {
										const item = messageQueue.find((m) => m.id === id);
										if (item) {
											// Remove from queue
											messageQueue = messageQueue.filter((m) => m.id !== id);
											// Set files and restore prompt to input
											files = item.files;
											messageInput?.setText(item.prompt);
										}
									}}
									onQueueDelete={(id) => {
										messageQueue = messageQueue.filter((m) => m.id !== id);
									}}
									onChange={(data) => {
										if (!$temporaryChatEnabled) {
											saveDraft(data, $chatId);
										}
									}}
									on:submit={async (e) => {
										clearDraft();
										if (e.detail || files.length > 0) {
											await tick();

											submitPrompt(e.detail.replaceAll('\n\n', '\n'));
										}
									}}
								/>

								<div
									class="absolute bottom-1 text-xs text-gray-500 text-center line-clamp-1 right-0 left-0"
								>
									<!-- {$i18n.t('LLMs can make mistakes. Verify important information.')} -->
								</div>
							</div>
						{:else}
							<div class="flex items-center h-full">
								<Placeholder
									{history}
									{selectedModels}
									thinkingEnabled={chatThinkingEnabled}
									{setChatThinkingEnabled}
									ledgerAgenticEnabled={chatLedgerAgenticEnabled}
									{setChatLedgerAgenticEnabled}
									focusedSearchEnabled={chatFocusedSearchEnabled}
									{setChatFocusedSearchEnabled}
									workingMode={chatWorkingMode}
									{setChatWorkingMode}
									localCorpusMode={chatLocalCorpusMode}
									{setChatLocalCorpusMode}
									scienceResearchMode={chatScienceResearchMode}
									{setChatScienceResearchMode}
									scienceAttachedCorpora={chatScienceAttachedCorpora}
									{setChatScienceAttachedCorpora}
									bind:messageInput
									bind:files
									bind:prompt
									bind:autoScroll
									bind:selectedToolIds
									bind:selectedFilterIds
									bind:imageGenerationEnabled
									bind:codeInterpreterEnabled
									bind:webSearchEnabled
									bind:atSelectedModel
									bind:showCommands
									bind:dragged
									toolServers={$toolServers}
									{stopResponse}
									{createMessagePair}
									{onSelect}
									{onUpload}
									onChange={(data) => {
										if (!$temporaryChatEnabled) {
											saveDraft(data);
										}
									}}
									on:submit={async (e) => {
										clearDraft();
										if (e.detail || files.length > 0) {
											await tick();
											submitPrompt(e.detail.replaceAll('\n\n', '\n'));
										}
									}}
								/>
							</div>
						{/if}
					</div>
				</Pane>

				<ChatControls
					bind:this={controlPaneComponent}
					bind:history
					bind:chatFiles
					bind:params
					bind:files
					bind:pane={controlPane}
					chatId={$chatId}
					modelId={selectedModelIds?.at(0) ?? null}
					voicePreference={activeVoicePreference}
					models={selectedModelIds.reduce((a, e, i, arr) => {
						const model = $models.find((m) => m.id === e);
						if (model) {
							return [...a, model];
						}
						return a;
					}, [])}
					{submitPrompt}
					{stopResponse}
					{showMessage}
					{eventTarget}
					{codeInterpreterEnabled}
				/>
			</PaneGroup>
		</div>
	{:else if loading}
		<div class=" flex items-center justify-center h-full w-full">
			<div class="m-auto">
				<Spinner className="size-5" />
			</div>
		</div>
	{/if}
</div>

<style>
	::-webkit-scrollbar {
		height: 0.5rem;
		width: 0.5rem;
	}
</style>

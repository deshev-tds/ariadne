<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { createEventDispatcher, onDestroy, onMount, getContext } from 'svelte';
	const dispatch = createEventDispatcher();

	import { getBackendConfig } from '$lib/apis';
	import {
		getAudioConfig,
		updateAudioConfig,
		getModels as _getModels,
		getVoices as _getVoices,
		synthesizeOpenAISpeech
	} from '$lib/apis/audio';
	import { uploadFile } from '$lib/apis/files';
	import { config, settings } from '$lib/stores';

	import Spinner from '$lib/components/common/Spinner.svelte';
	import SensitiveInput from '$lib/components/common/SensitiveInput.svelte';

	import { TTS_RESPONSE_SPLIT } from '$lib/types';

	import type { Writable } from 'svelte/store';
	import type { i18n as i18nType } from 'i18next';
	import Textarea from '$lib/components/common/Textarea.svelte';

	const i18n = getContext<Writable<i18nType>>('i18n');

	export let saveHandler: () => void;

	// Audio
	let TTS_OPENAI_API_BASE_URL = '';
	let TTS_OPENAI_API_KEY = '';
	let TTS_API_KEY = '';
	let TTS_ENGINE = '';
	let TTS_MODEL = '';
	let TTS_VOICE = '';
	let TTS_OPENAI_PARAMS = '';
	let TTS_SPLIT_ON: TTS_RESPONSE_SPLIT = TTS_RESPONSE_SPLIT.PUNCTUATION;
	let TTS_AZURE_SPEECH_REGION = '';
	let TTS_AZURE_SPEECH_BASE_URL = '';
	let TTS_AZURE_SPEECH_OUTPUT_FORMAT = '';

	let STT_OPENAI_API_BASE_URL = '';
	let STT_OPENAI_API_KEY = '';
	let STT_ENGINE = '';
	let STT_MODEL = '';
	let STT_SUPPORTED_CONTENT_TYPES = '';
	let STT_WHISPER_MODEL = '';
	let STT_AZURE_API_KEY = '';
	let STT_AZURE_REGION = '';
	let STT_AZURE_LOCALES = '';
	let STT_AZURE_BASE_URL = '';
	let STT_AZURE_MAX_SPEAKERS = '';
	let STT_DEEPGRAM_API_KEY = '';
	let STT_MISTRAL_API_KEY = '';
	let STT_MISTRAL_API_BASE_URL = '';
	let STT_MISTRAL_USE_CHAT_COMPLETIONS = false;

	let STT_WHISPER_MODEL_LOADING = false;

	type OmniVoicePreset = {
		name?: string;
		instruct?: string;
		ref_audio?: string;
		ref_audio_file_id?: string;
		ref_audio_filename?: string;
		ref_text?: string;
		speed?: number;
		num_step?: number;
		duration?: number;
	};

	type OmniVoiceParams = {
		voices?: Record<string, OmniVoicePreset>;
		device_map?: string;
		dtype?: string;
		attn_implementation?: string;
		speed?: number;
		num_step?: number;
		duration?: number;
	};

	const DEFAULT_OMNIVOICE_PREVIEW_TEXT =
		'This is a short OmniVoice preview so you can hear how the current tuning sounds.';

	let omniVoiceVoices: Record<string, OmniVoicePreset> = {};
	let omniVoiceDeviceMap = 'cuda:0';
	let omniVoiceDtype = 'float16';
	let omniVoiceAttnImplementation = '';
	let omniVoiceSpeed = '1';
	let omniVoiceNumStep = '4';
	let omniVoiceDuration = '';
	let omniVoiceExtraParams = '{}';

	let omniVoicePresetId = '';
	let omniVoicePresetName = '';
	let omniVoicePresetInstruct = '';
	let omniVoicePresetRefAudio = '';
	let omniVoicePresetRefAudioFileId = '';
	let omniVoicePresetRefAudioFilename = '';
	let omniVoicePresetRefText = '';
	let omniVoicePresetSpeed = '';
	let omniVoicePresetNumStep = '';
	let omniVoicePresetDuration = '';
	let omniVoiceRefAudioUploadLoading = false;
	let omniVoiceRefAudioInputElement: HTMLInputElement | null = null;

	let omniVoicePreviewText = DEFAULT_OMNIVOICE_PREVIEW_TEXT;
	let omniVoicePreviewLoading = false;
	let omniVoicePreviewAudio: HTMLAudioElement | null = null;
	let omniVoicePreviewUrl = '';

	// eslint-disable-next-line no-undef
	let voices: SpeechSynthesisVoice[] = [];
	let models: Awaited<ReturnType<typeof _getModels>>['models'] = [];

	const titleCaseId = (value: string) =>
		value
			.replace(/[_-]+/g, ' ')
			.trim()
			.replace(/\b\w/g, (char) => char.toUpperCase());

	const isPlainObject = (value: unknown): value is Record<string, unknown> =>
		typeof value === 'object' && value !== null && !Array.isArray(value);

	const parseOptionalNumber = (value: string, kind: 'float' | 'int' = 'float') => {
		const normalized = value.trim();
		if (!normalized) {
			return undefined;
		}

		const parsed = kind === 'int' ? parseInt(normalized, 10) : parseFloat(normalized);
		return Number.isFinite(parsed) ? parsed : undefined;
	};

	const toInputString = (value: unknown) =>
		typeof value === 'number' && Number.isFinite(value) ? String(value) : '';

	const cleanupOmniVoicePreviewAudio = () => {
		omniVoicePreviewAudio?.pause();
		omniVoicePreviewAudio = null;

		if (omniVoicePreviewUrl) {
			URL.revokeObjectURL(omniVoicePreviewUrl);
			omniVoicePreviewUrl = '';
		}
	};

	const loadOmniVoicePresetDraft = (presetId: string) => {
		const preset = omniVoiceVoices[presetId] ?? {};

		omniVoicePresetId = presetId;
		omniVoicePresetName = typeof preset.name === 'string' ? preset.name : titleCaseId(presetId);
		omniVoicePresetInstruct = typeof preset.instruct === 'string' ? preset.instruct : '';
		omniVoicePresetRefAudio = typeof preset.ref_audio === 'string' ? preset.ref_audio : '';
		omniVoicePresetRefAudioFileId =
			typeof preset.ref_audio_file_id === 'string' ? preset.ref_audio_file_id : '';
		omniVoicePresetRefAudioFilename =
			typeof preset.ref_audio_filename === 'string' ? preset.ref_audio_filename : '';
		omniVoicePresetRefText = typeof preset.ref_text === 'string' ? preset.ref_text : '';
		omniVoicePresetSpeed = toInputString(preset.speed);
		omniVoicePresetNumStep = toInputString(preset.num_step);
		omniVoicePresetDuration = toInputString(preset.duration);
	};

	const syncOmniVoiceEditorFromParams = (params: unknown) => {
		const normalized = isPlainObject(params) ? params : {};
		const voicesValue = isPlainObject(normalized.voices) ? normalized.voices : {};

		omniVoiceVoices = Object.fromEntries(
			Object.entries(voicesValue).map(([id, value]) => [
				id,
				isPlainObject(value) ? (value as OmniVoicePreset) : {}
			])
		);

		omniVoiceDeviceMap =
			typeof normalized.device_map === 'string' && normalized.device_map.trim()
				? normalized.device_map
				: 'cuda:0';
		omniVoiceDtype =
			typeof normalized.dtype === 'string' && normalized.dtype.trim()
				? normalized.dtype
				: 'float16';
		omniVoiceAttnImplementation =
			typeof normalized.attn_implementation === 'string' ? normalized.attn_implementation : '';
		omniVoiceSpeed = toInputString(normalized.speed) || '1';
		omniVoiceNumStep = toInputString(normalized.num_step) || '4';
		omniVoiceDuration = toInputString(normalized.duration);
		omniVoiceExtraParams = JSON.stringify(
			Object.fromEntries(
				Object.entries(normalized).filter(
					([key]) =>
						![
							'voices',
							'device_map',
							'dtype',
							'attn_implementation',
							'speed',
							'num_step',
							'duration'
						].includes(key)
				)
			),
			null,
			2
		);

		const preferredPresetId =
			(TTS_VOICE && omniVoiceVoices[TTS_VOICE] ? TTS_VOICE : '') ||
			Object.keys(omniVoiceVoices)[0] ||
			'auto';

		loadOmniVoicePresetDraft(preferredPresetId);
	};

	const buildOmniVoiceParams = (): OmniVoiceParams => {
		let extraParams: Record<string, unknown> = {};
		try {
			extraParams = omniVoiceExtraParams.trim() ? JSON.parse(omniVoiceExtraParams) : {};
		} catch (error) {
			throw new Error($i18n.t('Invalid JSON format for OmniVoice advanced parameters'));
		}

		if (!isPlainObject(extraParams)) {
			throw new Error($i18n.t('OmniVoice advanced parameters must be a JSON object'));
		}

		const params: OmniVoiceParams = {
			...(extraParams as OmniVoiceParams),
			voices: structuredClone(omniVoiceVoices)
		};

		if (omniVoiceDeviceMap.trim()) {
			params.device_map = omniVoiceDeviceMap.trim();
		}

		if (omniVoiceDtype.trim()) {
			params.dtype = omniVoiceDtype.trim();
		}

		if (omniVoiceAttnImplementation.trim()) {
			params.attn_implementation = omniVoiceAttnImplementation.trim();
		}

		const speed = parseOptionalNumber(omniVoiceSpeed);
		if (speed !== undefined) {
			params.speed = speed;
		}

		const numStep = parseOptionalNumber(omniVoiceNumStep, 'int');
		if (numStep !== undefined) {
			params.num_step = numStep;
		}

		const duration = parseOptionalNumber(omniVoiceDuration);
		if (duration !== undefined) {
			params.duration = duration;
		}

		if (!Object.keys(params.voices ?? {}).length) {
			delete params.voices;
		}

		return params;
	};

	const syncTTSParamsFromOmniVoiceEditor = () => {
		TTS_OPENAI_PARAMS = JSON.stringify(buildOmniVoiceParams(), null, 2);
	};

	const saveOmniVoicePreset = (showToast = true) => {
		const presetId = omniVoicePresetId.trim();
		if (!presetId) {
			if (
				omniVoicePresetName.trim() ||
				omniVoicePresetInstruct.trim() ||
				omniVoicePresetRefAudio.trim() ||
				omniVoicePresetRefAudioFileId.trim() ||
				omniVoicePresetRefText.trim() ||
				omniVoicePresetSpeed.trim() ||
				omniVoicePresetNumStep.trim() ||
				omniVoicePresetDuration.trim()
			) {
				toast.error($i18n.t('Preset ID is required'));
				return false;
			}

			return true;
		}

		const preset: OmniVoicePreset = {};

		if (omniVoicePresetName.trim()) {
			preset.name = omniVoicePresetName.trim();
		}
		if (omniVoicePresetInstruct.trim()) {
			preset.instruct = omniVoicePresetInstruct.trim();
		}
		if (omniVoicePresetRefAudio.trim()) {
			preset.ref_audio = omniVoicePresetRefAudio.trim();
		}
		if (omniVoicePresetRefAudioFileId.trim()) {
			preset.ref_audio_file_id = omniVoicePresetRefAudioFileId.trim();
		}
		if (omniVoicePresetRefAudioFilename.trim()) {
			preset.ref_audio_filename = omniVoicePresetRefAudioFilename.trim();
		}
		if (omniVoicePresetRefText.trim()) {
			preset.ref_text = omniVoicePresetRefText.trim();
		}

		const speed = parseOptionalNumber(omniVoicePresetSpeed);
		if (speed !== undefined) {
			preset.speed = speed;
		}

		const numStep = parseOptionalNumber(omniVoicePresetNumStep, 'int');
		if (numStep !== undefined) {
			preset.num_step = numStep;
		}

		const duration = parseOptionalNumber(omniVoicePresetDuration);
		if (duration !== undefined) {
			preset.duration = duration;
		}

		omniVoiceVoices = {
			...omniVoiceVoices,
			[presetId]: preset
		};
		TTS_VOICE = presetId;
		loadOmniVoicePresetDraft(presetId);
		syncTTSParamsFromOmniVoiceEditor();
		if (showToast) {
			toast.success($i18n.t('Preset saved locally. Save settings to apply it.'));
		}
		return true;
	};

	const newOmniVoicePreset = () => {
		omniVoicePresetId = '';
		omniVoicePresetName = '';
		omniVoicePresetInstruct = '';
		omniVoicePresetRefAudio = '';
		omniVoicePresetRefAudioFileId = '';
		omniVoicePresetRefAudioFilename = '';
		omniVoicePresetRefText = '';
		omniVoicePresetSpeed = '';
		omniVoicePresetNumStep = '';
		omniVoicePresetDuration = '';
	};

	const triggerOmniVoiceRefAudioUpload = () => {
		omniVoiceRefAudioInputElement?.click();
	};

	const clearOmniVoiceRefAudio = () => {
		omniVoicePresetRefAudio = '';
		omniVoicePresetRefAudioFileId = '';
		omniVoicePresetRefAudioFilename = '';
		if (omniVoiceRefAudioInputElement) {
			omniVoiceRefAudioInputElement.value = '';
		}
	};

	const uploadOmniVoiceRefAudio = async (file: File | null) => {
		if (!file) {
			return;
		}

		omniVoiceRefAudioUploadLoading = true;

		try {
			const uploadedFile = await uploadFile(
				localStorage.token,
				file,
				{
					context: 'omnivoice_reference_audio'
				},
				false
			);

			if (!uploadedFile?.id) {
				throw new Error($i18n.t('Failed to upload file.'));
			}

			omniVoicePresetRefAudio = '';
			omniVoicePresetRefAudioFileId = `${uploadedFile.id}`;
			omniVoicePresetRefAudioFilename = `${uploadedFile.filename ?? file.name}`;
			toast.success($i18n.t('Reference audio uploaded'));
		} catch (error) {
			console.error(error);
			toast.error(`${error}`);
		} finally {
			omniVoiceRefAudioUploadLoading = false;
			if (omniVoiceRefAudioInputElement) {
				omniVoiceRefAudioInputElement.value = '';
			}
		}
	};

	const deleteOmniVoicePreset = () => {
		const presetId = omniVoicePresetId.trim();
		if (!presetId || !omniVoiceVoices[presetId]) {
			return;
		}

		const nextVoices = { ...omniVoiceVoices };
		delete nextVoices[presetId];
		omniVoiceVoices = nextVoices;

		const nextPresetId = Object.keys(nextVoices)[0] || 'auto';
		if (TTS_VOICE === presetId) {
			TTS_VOICE = nextPresetId;
		}

		if (nextPresetId !== 'auto') {
			loadOmniVoicePresetDraft(nextPresetId);
		} else {
			newOmniVoicePreset();
			omniVoicePresetId = 'auto';
			omniVoicePresetName = 'Auto';
		}

		syncTTSParamsFromOmniVoiceEditor();
		toast.success($i18n.t('Preset removed locally. Save settings to apply it.'));
	};

	const setTTSDefaultsForEngine = (engine: string) => {
		if (engine === 'openai') {
			TTS_VOICE = 'alloy';
			TTS_MODEL = 'tts-1';
			return;
		}

		if (engine === 'kokoro_onnx') {
			if (['', 'alloy', 'echo', 'fable', 'onyx', 'nova', 'shimmer', 'auto'].includes(TTS_VOICE)) {
				TTS_VOICE = 'bm_fable';
			}

			if (
				!TTS_MODEL ||
				['tts-1', 'tts-1-hd', 'k2-fsa/OmniVoice'].includes(TTS_MODEL)
			) {
				TTS_MODEL = 'backend/models/kokoro-v1.0.onnx';
			}

			try {
				const parsed = TTS_OPENAI_PARAMS ? JSON.parse(TTS_OPENAI_PARAMS) : {};
				const nextParams = isPlainObject(parsed) ? { ...parsed } : {};

				delete nextParams.device_map;
				delete nextParams.dtype;
				delete nextParams.attn_implementation;
				delete nextParams.num_step;
				delete nextParams.duration;
				delete nextParams.voices;

				if (!nextParams.voices_path) {
					nextParams.voices_path = 'backend/models/voices-v1.0.bin';
				}
				if (!nextParams.lang) {
					nextParams.lang = 'en-us';
				}
				if (nextParams.speed === undefined) {
					nextParams.speed = 1.0;
				}

				TTS_OPENAI_PARAMS = JSON.stringify(nextParams, null, 2);
			} catch {
				TTS_OPENAI_PARAMS = JSON.stringify(
					{
						voices_path: 'backend/models/voices-v1.0.bin',
						lang: 'en-us',
						speed: 1.0
					},
					null,
					2
				);
			}

			return;
		}

		if (engine === 'omnivoice') {
			if (!TTS_VOICE || ['alloy', 'bm_fable'].includes(TTS_VOICE)) {
				TTS_VOICE = 'auto';
			}
			if (
				!TTS_MODEL ||
				['tts-1', 'tts-1-hd', 'backend/models/kokoro-v1.0.onnx'].includes(TTS_MODEL)
			) {
				TTS_MODEL = 'k2-fsa/OmniVoice';
			}
			if (!TTS_OPENAI_PARAMS.trim()) {
				syncTTSParamsFromOmniVoiceEditor();
			}
			try {
				syncOmniVoiceEditorFromParams(TTS_OPENAI_PARAMS ? JSON.parse(TTS_OPENAI_PARAMS) : {});
			} catch {
				syncOmniVoiceEditorFromParams({});
			}
			return;
		}

		TTS_VOICE = '';
		TTS_MODEL = '';
	};

	const getModels = async () => {
		if (TTS_ENGINE === '') {
			models = [];
		} else {
			const res = await _getModels(
				localStorage.token,
				$config?.features?.enable_direct_connections && ($settings?.directConnections ?? null)
			).catch((e) => {
				toast.error(`${e}`);
			});

			if (res) {
				console.log(res);
				models = res.models;
			}
		}
	};

	const getVoices = async () => {
		if (TTS_ENGINE === '') {
			const getVoicesLoop = setInterval(() => {
				voices = speechSynthesis.getVoices();

				// do your loop
				if (voices.length > 0) {
					clearInterval(getVoicesLoop);
					voices.sort((a, b) => a.name.localeCompare(b.name, $i18n.resolvedLanguage));
				}
			}, 100);
		} else {
			const res = await _getVoices(localStorage.token).catch((e) => {
				toast.error(`${e}`);
			});

			if (res) {
				console.log(res);
				voices = res.voices;
				voices.sort((a, b) => a.name.localeCompare(b.name, $i18n.resolvedLanguage));
			}
		}
	};

	const updateConfigHandler = async () => {
		if (TTS_ENGINE === 'omnivoice') {
			const presetSaved = saveOmniVoicePreset(false);
			if (!presetSaved) {
				return false;
			}
			try {
				syncTTSParamsFromOmniVoiceEditor();
			} catch (error) {
				toast.error(`${error}`);
				return false;
			}
		}

		let openaiParams = {};
		try {
			openaiParams = TTS_OPENAI_PARAMS ? JSON.parse(TTS_OPENAI_PARAMS) : {};
			TTS_OPENAI_PARAMS = JSON.stringify(openaiParams, null, 2);
		} catch (e) {
			toast.error($i18n.t('Invalid JSON format for Parameters'));
			return;
		}

		const res = await updateAudioConfig(localStorage.token, {
			tts: {
				OPENAI_API_BASE_URL: TTS_OPENAI_API_BASE_URL,
				OPENAI_API_KEY: TTS_OPENAI_API_KEY,
				OPENAI_PARAMS: openaiParams,
				API_KEY: TTS_API_KEY,
				ENGINE: TTS_ENGINE,
				MODEL: TTS_MODEL,
				VOICE: TTS_VOICE,
				AZURE_SPEECH_REGION: TTS_AZURE_SPEECH_REGION,
				AZURE_SPEECH_BASE_URL: TTS_AZURE_SPEECH_BASE_URL,
				AZURE_SPEECH_OUTPUT_FORMAT: TTS_AZURE_SPEECH_OUTPUT_FORMAT,
				SPLIT_ON: TTS_SPLIT_ON
			},
			stt: {
				OPENAI_API_BASE_URL: STT_OPENAI_API_BASE_URL,
				OPENAI_API_KEY: STT_OPENAI_API_KEY,
				ENGINE: STT_ENGINE,
				MODEL: STT_MODEL,
				SUPPORTED_CONTENT_TYPES: STT_SUPPORTED_CONTENT_TYPES.split(','),
				WHISPER_MODEL: STT_WHISPER_MODEL,
				DEEPGRAM_API_KEY: STT_DEEPGRAM_API_KEY,
				AZURE_API_KEY: STT_AZURE_API_KEY,
				AZURE_REGION: STT_AZURE_REGION,
				AZURE_LOCALES: STT_AZURE_LOCALES,
				AZURE_BASE_URL: STT_AZURE_BASE_URL,
				AZURE_MAX_SPEAKERS: STT_AZURE_MAX_SPEAKERS,
				MISTRAL_API_KEY: STT_MISTRAL_API_KEY,
				MISTRAL_API_BASE_URL: STT_MISTRAL_API_BASE_URL,
				MISTRAL_USE_CHAT_COMPLETIONS: STT_MISTRAL_USE_CHAT_COMPLETIONS
			}
		});

		if (res) {
			saveHandler();
			config.set(await getBackendConfig());
			await getVoices();
			await getModels();
			return true;
		}

		return false;
	};

	const previewOmniVoiceHandler = async () => {
		if (TTS_ENGINE !== 'omnivoice') {
			return;
		}

		const previewText = omniVoicePreviewText.trim();
		if (!previewText) {
			toast.error($i18n.t('Preview text is required'));
			return;
		}

		const saved = await updateConfigHandler();
		if (!saved) {
			return;
		}

		omniVoicePreviewLoading = true;

		try {
			cleanupOmniVoicePreviewAudio();

			const res = await synthesizeOpenAISpeech(
				localStorage.token,
				TTS_VOICE || omniVoicePresetId || 'auto',
				previewText
			);

			if (!res) {
				return;
			}

			const blob = await res.blob();
			const url = URL.createObjectURL(blob);
			const audio = new Audio(url);
			omniVoicePreviewUrl = url;
			audio.onended = cleanupOmniVoicePreviewAudio;
			audio.onerror = cleanupOmniVoicePreviewAudio;

			omniVoicePreviewAudio = audio;
			await audio.play();
		} catch (error) {
			console.error(error);
			toast.error(`${error}`);
		} finally {
			omniVoicePreviewLoading = false;
		}
	};

	const sttModelUpdateHandler = async () => {
		STT_WHISPER_MODEL_LOADING = true;
		await updateConfigHandler();
		STT_WHISPER_MODEL_LOADING = false;
	};

	onMount(async () => {
		const res = await getAudioConfig(localStorage.token);

		if (res) {
			console.log(res);
			TTS_OPENAI_API_BASE_URL = res.tts.OPENAI_API_BASE_URL;
			TTS_OPENAI_API_KEY = res.tts.OPENAI_API_KEY;
			TTS_OPENAI_PARAMS = JSON.stringify(res?.tts?.OPENAI_PARAMS ?? '', null, 2);
			TTS_API_KEY = res.tts.API_KEY;

			TTS_ENGINE = res.tts.ENGINE;
			TTS_MODEL = res.tts.MODEL;
			TTS_VOICE = res.tts.VOICE;
			syncOmniVoiceEditorFromParams(res?.tts?.OPENAI_PARAMS);

			TTS_SPLIT_ON = res.tts.SPLIT_ON || TTS_RESPONSE_SPLIT.PUNCTUATION;

			TTS_AZURE_SPEECH_REGION = res.tts.AZURE_SPEECH_REGION;
			TTS_AZURE_SPEECH_BASE_URL = res.tts.AZURE_SPEECH_BASE_URL;
			TTS_AZURE_SPEECH_OUTPUT_FORMAT = res.tts.AZURE_SPEECH_OUTPUT_FORMAT;

			STT_OPENAI_API_BASE_URL = res.stt.OPENAI_API_BASE_URL;
			STT_OPENAI_API_KEY = res.stt.OPENAI_API_KEY;

			STT_ENGINE = res.stt.ENGINE;
			STT_MODEL = res.stt.MODEL;
			STT_SUPPORTED_CONTENT_TYPES = (res?.stt?.SUPPORTED_CONTENT_TYPES ?? []).join(',');
			STT_WHISPER_MODEL = res.stt.WHISPER_MODEL;
			STT_AZURE_API_KEY = res.stt.AZURE_API_KEY;
			STT_AZURE_REGION = res.stt.AZURE_REGION;
			STT_AZURE_LOCALES = res.stt.AZURE_LOCALES;
			STT_AZURE_BASE_URL = res.stt.AZURE_BASE_URL;
			STT_AZURE_MAX_SPEAKERS = res.stt.AZURE_MAX_SPEAKERS;
			STT_DEEPGRAM_API_KEY = res.stt.DEEPGRAM_API_KEY;
			STT_MISTRAL_API_KEY = res.stt.MISTRAL_API_KEY;
			STT_MISTRAL_API_BASE_URL = res.stt.MISTRAL_API_BASE_URL;
			STT_MISTRAL_USE_CHAT_COMPLETIONS = res.stt.MISTRAL_USE_CHAT_COMPLETIONS;
		}

		await getVoices();
		await getModels();
	});

	onDestroy(() => {
		cleanupOmniVoicePreviewAudio();
	});
</script>

<form
	class="flex flex-col h-full justify-between space-y-3 text-sm"
	on:submit|preventDefault={async () => {
		await updateConfigHandler();
		dispatch('save');
	}}
>
	<div class=" space-y-3 overflow-y-scroll scrollbar-hidden h-full">
		<div class="flex flex-col gap-3">
			<div>
				<div class=" mt-0.5 mb-2.5 text-base font-medium">{$i18n.t('Speech-to-Text')}</div>

				<hr class=" border-gray-100/30 dark:border-gray-850/30 my-2" />

				{#if STT_ENGINE !== 'web'}
					<div class="mb-2">
						<div class=" mb-1.5 text-xs font-medium">{$i18n.t('Supported MIME Types')}</div>
						<div class="flex w-full">
							<div class="flex-1">
								<input
									class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
									bind:value={STT_SUPPORTED_CONTENT_TYPES}
									placeholder={$i18n.t(
										'e.g., audio/wav,audio/mpeg,video/* (leave blank for defaults)'
									)}
								/>
							</div>
						</div>
					</div>
				{/if}

				<div class="mb-2 py-0.5 flex w-full justify-between">
					<div class=" self-center text-xs font-medium">{$i18n.t('Speech-to-Text Engine')}</div>
					<div class="flex items-center relative">
						<select
							class="cursor-pointer w-fit pr-8 rounded-sm px-2 p-1 text-xs bg-transparent outline-hidden text-right"
							bind:value={STT_ENGINE}
							placeholder={$i18n.t('Select an engine')}
						>
							<option value="">{$i18n.t('Whisper (Local)')}</option>
							<option value="openai">{$i18n.t('OpenAI')}</option>
							<option value="web">{$i18n.t('Web API')}</option>
							<option value="deepgram">{$i18n.t('Deepgram')}</option>
							<option value="azure">{$i18n.t('Azure AI Speech')}</option>
							<option value="mistral">{$i18n.t('MistralAI')}</option>
						</select>
					</div>
				</div>

				{#if STT_ENGINE === 'openai'}
					<div>
						<div class="mt-1 flex gap-2 mb-1">
							<input
								class="flex-1 w-full bg-transparent outline-hidden"
								placeholder={$i18n.t('API Base URL')}
								bind:value={STT_OPENAI_API_BASE_URL}
								required
							/>

							<SensitiveInput placeholder={$i18n.t('API Key')} bind:value={STT_OPENAI_API_KEY} />
						</div>
					</div>

					<hr class="border-gray-100/30 dark:border-gray-850/30 my-2" />

					<div>
						<div class=" mb-1.5 text-xs font-medium">{$i18n.t('STT Model')}</div>
						<div class="flex w-full">
							<div class="flex-1">
								<input
									list="model-list"
									class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
									bind:value={STT_MODEL}
									placeholder={$i18n.t('Select a model')}
								/>

								<datalist id="model-list">
									<option value="whisper-1" />
								</datalist>
							</div>
						</div>
					</div>
				{:else if STT_ENGINE === 'deepgram'}
					<div>
						<div class="mt-1 flex gap-2 mb-1">
							<SensitiveInput placeholder={$i18n.t('API Key')} bind:value={STT_DEEPGRAM_API_KEY} />
						</div>
					</div>

					<hr class="border-gray-100/30 dark:border-gray-850/30 my-2" />

					<div>
						<div class=" mb-1.5 text-xs font-medium">{$i18n.t('STT Model')}</div>
						<div class="flex w-full">
							<div class="flex-1">
								<input
									class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
									bind:value={STT_MODEL}
									placeholder={$i18n.t('Select a model (optional)')}
								/>
							</div>
						</div>
						<div class="mt-2 mb-1 text-xs text-gray-400 dark:text-gray-500">
							{$i18n.t('Leave model field empty to use the default model.')}
							<a
								class=" hover:underline dark:text-gray-200 text-gray-800"
								href="https://developers.deepgram.com/docs/models"
								target="_blank"
							>
								{$i18n.t('Click here to see available models.')}
							</a>
						</div>
					</div>
				{:else if STT_ENGINE === 'azure'}
					<div>
						<div class="mt-1 flex gap-2 mb-1">
							<SensitiveInput
								placeholder={$i18n.t('API Key')}
								bind:value={STT_AZURE_API_KEY}
								required
							/>
						</div>

						<hr class="border-gray-100/30 dark:border-gray-850/30 my-2" />

						<div>
							<div class=" mb-1.5 text-xs font-medium">{$i18n.t('Azure Region')}</div>
							<div class="flex w-full">
								<div class="flex-1">
									<input
										class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
										bind:value={STT_AZURE_REGION}
										placeholder={$i18n.t('e.g., westus (leave blank for eastus)')}
									/>
								</div>
							</div>
						</div>

						<div>
							<div class=" mb-1.5 text-xs font-medium">{$i18n.t('Language Locales')}</div>
							<div class="flex w-full">
								<div class="flex-1">
									<input
										class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
										bind:value={STT_AZURE_LOCALES}
										placeholder={$i18n.t('e.g., en-US,ja-JP (leave blank for auto-detect)')}
									/>
								</div>
							</div>
						</div>

						<div>
							<div class=" mb-1.5 text-xs font-medium">{$i18n.t('Endpoint URL')}</div>
							<div class="flex w-full">
								<div class="flex-1">
									<input
										class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
										bind:value={STT_AZURE_BASE_URL}
										placeholder={$i18n.t('(leave blank for to use commercial endpoint)')}
									/>
								</div>
							</div>
						</div>

						<div>
							<div class=" mb-1.5 text-xs font-medium">{$i18n.t('Max Speakers')}</div>
							<div class="flex w-full">
								<div class="flex-1">
									<input
										class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
										bind:value={STT_AZURE_MAX_SPEAKERS}
										placeholder={$i18n.t('e.g., 3, 4, 5 (leave blank for default)')}
									/>
								</div>
							</div>
						</div>
					</div>
				{:else if STT_ENGINE === 'mistral'}
					<div>
						<div class="mt-1 flex gap-2 mb-1">
							<input
								class="flex-1 w-full bg-transparent outline-hidden"
								placeholder={$i18n.t('API Base URL')}
								bind:value={STT_MISTRAL_API_BASE_URL}
								required
							/>

							<SensitiveInput placeholder={$i18n.t('API Key')} bind:value={STT_MISTRAL_API_KEY} />
						</div>
					</div>

					<hr class="border-gray-100/30 dark:border-gray-850/30 my-2" />

					<div>
						<div class=" mb-1.5 text-xs font-medium">{$i18n.t('STT Model')}</div>
						<div class="flex w-full">
							<div class="flex-1">
								<input
									class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
									bind:value={STT_MODEL}
									placeholder="voxtral-mini-latest"
								/>
							</div>
						</div>
						<div class="mt-2 mb-1 text-xs text-gray-400 dark:text-gray-500">
							{$i18n.t('Leave empty to use the default model (voxtral-mini-latest).')}
							<a
								class=" hover:underline dark:text-gray-200 text-gray-800"
								href="https://docs.mistral.ai/capabilities/audio_transcription"
								target="_blank"
							>
								{$i18n.t('Learn more about Voxtral transcription.')}
							</a>
						</div>
					</div>

					<hr class="border-gray-100/30 dark:border-gray-850/30 my-2" />

					<div>
						<div class="flex items-center justify-between mb-2">
							<div class="text-xs font-medium">{$i18n.t('Use Chat Completions API')}</div>
							<label class="relative inline-flex items-center cursor-pointer">
								<input
									type="checkbox"
									bind:checked={STT_MISTRAL_USE_CHAT_COMPLETIONS}
									class="sr-only peer"
								/>
								<div
									class="w-9 h-5 bg-gray-200 peer-focus:outline-none peer-focus:ring-2 peer-focus:ring-blue-300 dark:peer-focus:ring-blue-800 rounded-full peer dark:bg-gray-700 peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all dark:border-gray-600 peer-checked:bg-blue-600"
								></div>
							</label>
						</div>
						<div class="text-xs text-gray-400 dark:text-gray-500">
							{$i18n.t(
								'Use /v1/chat/completions endpoint instead of /v1/audio/transcriptions for potentially better accuracy.'
							)}
						</div>
					</div>
				{:else if STT_ENGINE === ''}
					<div>
						<div class=" mb-1.5 text-xs font-medium">{$i18n.t('STT Model')}</div>

						<div class="flex w-full">
							<div class="flex-1 mr-2">
								<input
									class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
									placeholder={$i18n.t('Set whisper model')}
									bind:value={STT_WHISPER_MODEL}
								/>
							</div>

							<button
								class="px-2.5 bg-gray-50 hover:bg-gray-200 text-gray-800 dark:bg-gray-850 dark:hover:bg-gray-800 dark:text-gray-100 rounded-lg transition"
								on:click={() => {
									sttModelUpdateHandler();
								}}
								disabled={STT_WHISPER_MODEL_LOADING}
							>
								{#if STT_WHISPER_MODEL_LOADING}
									<div class="self-center">
										<Spinner />
									</div>
								{:else}
									<svg
										xmlns="http://www.w3.org/2000/svg"
										viewBox="0 0 16 16"
										fill="currentColor"
										class="w-4 h-4"
									>
										<path
											d="M8.75 2.75a.75.75 0 0 0-1.5 0v5.69L5.03 6.22a.75.75 0 0 0-1.06 1.06l3.5 3.5a.75.75 0 0 0 1.06 0l3.5-3.5a.75.75 0 0 0-1.06-1.06L8.75 8.44V2.75Z"
										/>
										<path
											d="M3.5 9.75a.75.75 0 0 0-1.5 0v1.5A2.75 2.75 0 0 0 4.75 14h6.5A2.75 2.75 0 0 0 14 11.25v-1.5a.75.75 0 0 0-1.5 0v1.5c0 .69-.56 1.25-1.25 1.25h-6.5c-.69 0-1.25-.56-1.25-1.25v-1.5Z"
										/>
									</svg>
								{/if}
							</button>
						</div>

						<div class="mt-2 mb-1 text-xs text-gray-400 dark:text-gray-500">
							{$i18n.t(`Open WebUI uses faster-whisper internally.`)}

							<a
								class=" hover:underline dark:text-gray-200 text-gray-800"
								href="https://github.com/SYSTRAN/faster-whisper"
								target="_blank"
							>
								{$i18n.t(
									`Click here to learn more about faster-whisper and see the available models.`
								)}
							</a>
						</div>
					</div>
				{/if}
			</div>

			<div>
				<div class=" mt-0.5 mb-2.5 text-base font-medium">{$i18n.t('Text-to-Speech')}</div>

				<hr class=" border-gray-100/30 dark:border-gray-850/30 my-2" />

				<div class="mb-2 py-0.5 flex w-full justify-between">
					<div class=" self-center text-xs font-medium">{$i18n.t('Text-to-Speech Engine')}</div>
					<div class="flex items-center relative">
						<select
							class="w-fit pr-8 cursor-pointer rounded-sm px-2 p-1 text-xs bg-transparent outline-hidden text-right"
							bind:value={TTS_ENGINE}
							placeholder={$i18n.t('Select a mode')}
							on:change={async (e) => {
								setTTSDefaultsForEngine(e.target?.value ?? '');
								await updateConfigHandler();
								await getVoices();
								await getModels();
							}}
						>
							<option value="">{$i18n.t('Web API')}</option>
							<option value="transformers">{$i18n.t('Transformers')} ({$i18n.t('Local')})</option>
							<option value="openai">{$i18n.t('OpenAI')}</option>
							<option value="kokoro_onnx">{$i18n.t('Kokoro ONNX')} ({$i18n.t('Local')})</option>
							<option value="omnivoice">{$i18n.t('OmniVoice')} ({$i18n.t('Local')})</option>
							<option value="elevenlabs">{$i18n.t('ElevenLabs')}</option>
							<option value="azure">{$i18n.t('Azure AI Speech')}</option>
						</select>
					</div>
				</div>

				{#if TTS_ENGINE === 'openai'}
					<div>
						<div class="mt-1 flex gap-2 mb-1">
							<input
								class="flex-1 w-full bg-transparent outline-hidden"
								placeholder={$i18n.t('API Base URL')}
								bind:value={TTS_OPENAI_API_BASE_URL}
								required
							/>

							<SensitiveInput placeholder={$i18n.t('API Key')} bind:value={TTS_OPENAI_API_KEY} />
						</div>
					</div>
				{:else if TTS_ENGINE === 'elevenlabs'}
					<div>
						<div class="mt-1 flex gap-2 mb-1">
							<SensitiveInput placeholder={$i18n.t('API Key')} bind:value={TTS_API_KEY} required />
						</div>
					</div>
				{:else if TTS_ENGINE === 'azure'}
					<div>
						<div class="mt-1 flex gap-2 mb-1">
							<SensitiveInput placeholder={$i18n.t('API Key')} bind:value={TTS_API_KEY} required />
						</div>

						<hr class="border-gray-100/30 dark:border-gray-850/30 my-2" />

						<div>
							<div class=" mb-1.5 text-xs font-medium">{$i18n.t('Azure Region')}</div>
							<div class="flex w-full">
								<div class="flex-1">
									<input
										class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
										bind:value={TTS_AZURE_SPEECH_REGION}
										placeholder={$i18n.t('e.g., westus (leave blank for eastus)')}
									/>
								</div>
							</div>
						</div>

						<div>
							<div class=" mb-1.5 text-xs font-medium">{$i18n.t('Endpoint URL')}</div>
							<div class="flex w-full">
								<div class="flex-1">
									<input
										class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
										bind:value={TTS_AZURE_SPEECH_BASE_URL}
										placeholder={$i18n.t('(leave blank for to use commercial endpoint)')}
									/>
								</div>
							</div>
						</div>
					</div>
				{/if}

				<div class="mb-2">
					{#if TTS_ENGINE === ''}
						<div>
							<div class=" mb-1.5 text-xs font-medium">{$i18n.t('TTS Voice')}</div>
							<div class="flex w-full">
								<div class="flex-1">
									<select
										class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
										bind:value={TTS_VOICE}
									>
										<option value="" selected={TTS_VOICE !== ''}>{$i18n.t('Default')}</option>
										{#each voices as voice}
											<option
												value={voice.voiceURI}
												class="bg-gray-100 dark:bg-gray-700"
												selected={TTS_VOICE === voice.voiceURI}>{voice.name}</option
											>
										{/each}
									</select>
								</div>
							</div>
						</div>
					{:else if TTS_ENGINE === 'transformers'}
						<div>
							<div class=" mb-1.5 text-xs font-medium">{$i18n.t('TTS Model')}</div>
							<div class="flex w-full">
								<div class="flex-1">
									<input
										list="model-list"
										class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
										bind:value={TTS_MODEL}
										placeholder={$i18n.t('CMU ARCTIC speaker embedding name')}
									/>

									<datalist id="model-list">
										<option value="tts-1" />
									</datalist>
								</div>
							</div>
							<div class="mt-2 mb-1 text-xs text-gray-400 dark:text-gray-500">
								{$i18n.t(`Open WebUI uses SpeechT5 and CMU Arctic speaker embeddings.`)}

								To learn more about SpeechT5,

								<a
									class=" hover:underline dark:text-gray-200 text-gray-800"
									href="https://github.com/microsoft/SpeechT5"
									target="_blank"
								>
									{$i18n.t(`click here`, {
										name: 'SpeechT5'
									})}.
								</a>
								To see the available CMU Arctic speaker embeddings,
								<a
									class=" hover:underline dark:text-gray-200 text-gray-800"
									href="https://huggingface.co/datasets/Matthijs/cmu-arctic-xvectors"
									target="_blank"
								>
									{$i18n.t(`click here`)}.
								</a>
							</div>
						</div>
					{:else if TTS_ENGINE === 'openai'}
						<div class=" flex gap-2">
							<div class="w-full">
								<div class=" mb-1.5 text-xs font-medium">{$i18n.t('TTS Voice')}</div>
								<div class="flex w-full">
									<div class="flex-1">
										<input
											list="voice-list"
											class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
											bind:value={TTS_VOICE}
											placeholder={$i18n.t('Select a voice')}
										/>

										<datalist id="voice-list">
											{#each voices as voice}
												<option value={voice.id}>{voice.name}</option>
											{/each}
										</datalist>
									</div>
								</div>
							</div>
							<div class="w-full">
								<div class=" mb-1.5 text-xs font-medium">{$i18n.t('TTS Model')}</div>
								<div class="flex w-full">
									<div class="flex-1">
										<input
											list="tts-model-list"
											class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
											bind:value={TTS_MODEL}
											placeholder={$i18n.t('Select a model')}
										/>

										<datalist id="tts-model-list">
											{#each models as model}
												<option value={model.id} class="bg-gray-50 dark:bg-gray-700" />
											{/each}
										</datalist>
									</div>
								</div>
							</div>
						</div>

						<div class="mt-2 mb-1 text-xs text-gray-400 dark:text-gray-500">
							<div class="w-full">
								<div class=" mb-1.5 text-xs font-medium">{$i18n.t('Additional Parameters')}</div>
								<div class="flex w-full">
									<div class="flex-1">
										<Textarea
											className="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
											bind:value={TTS_OPENAI_PARAMS}
											placeholder={$i18n.t('Enter additional parameters in JSON format')}
											minSize={100}
										/>
									</div>
								</div>
							</div>
						</div>
					{:else if TTS_ENGINE === 'kokoro_onnx'}
						<div class=" flex gap-2">
							<div class="w-full">
								<div class=" mb-1.5 text-xs font-medium">{$i18n.t('TTS Voice')}</div>
								<div class="flex w-full">
									<div class="flex-1">
										<input
											list="voice-list"
											class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
											bind:value={TTS_VOICE}
											placeholder={$i18n.t('Select a voice')}
										/>

										<datalist id="voice-list">
											{#each voices as voice}
												<option value={voice.id}>{voice.name}</option>
											{/each}
										</datalist>
									</div>
								</div>
							</div>

							<div class="w-full">
								<div class=" mb-1.5 text-xs font-medium">{$i18n.t('Model Path')}</div>
								<div class="flex w-full">
									<div class="flex-1">
										<input
											list="tts-model-list"
											class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
											bind:value={TTS_MODEL}
											placeholder={$i18n.t('Path to kokoro-v1.0.onnx')}
										/>

										<datalist id="tts-model-list">
											{#each models as model}
												<option value={model.id} class="bg-gray-50 dark:bg-gray-700" />
											{/each}
										</datalist>
									</div>
								</div>
							</div>
						</div>

						<div class="mt-2 mb-1 text-xs text-gray-400 dark:text-gray-500">
							<div class="w-full">
								<div class=" mb-1.5 text-xs font-medium">{$i18n.t('Additional Parameters')}</div>
								<div class="flex w-full">
									<div class="flex-1">
										<Textarea
											className="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
											bind:value={TTS_OPENAI_PARAMS}
											placeholder={$i18n.t(
												'JSON: {"voices_path":"backend/models/voices-v1.0.bin","lang":"en-us","speed":1.0}'
											)}
											minSize={100}
										/>
									</div>
								</div>
							</div>

							<div class="mt-2">
								{$i18n.t(
									'Kokoro uses `model` and `voice` above. Optional JSON keys: `voices_path`, `lang`, `speed`, `voice`, and `voices`.'
								)}
							</div>
						</div>
					{:else if TTS_ENGINE === 'omnivoice'}
						<div class=" flex gap-2">
							<div class="w-full">
								<div class=" mb-1.5 text-xs font-medium">{$i18n.t('TTS Voice')}</div>
								<div class="flex w-full">
									<div class="flex-1">
										<input
											list="voice-list"
											class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
											bind:value={TTS_VOICE}
											placeholder={$i18n.t('Select a voice')}
											on:change={() => {
												if (omniVoiceVoices[TTS_VOICE]) {
													loadOmniVoicePresetDraft(TTS_VOICE);
												}
											}}
										/>

										<datalist id="voice-list">
											{#each voices as voice}
												<option value={voice.id}>{voice.name}</option>
											{/each}
										</datalist>
									</div>
								</div>
							</div>

							<div class="w-full">
								<div class=" mb-1.5 text-xs font-medium">{$i18n.t('Model ID')}</div>
								<div class="flex w-full">
									<div class="flex-1">
										<input
											list="tts-model-list"
											class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
											bind:value={TTS_MODEL}
											placeholder="k2-fsa/OmniVoice"
										/>

										<datalist id="tts-model-list">
											{#each models as model}
												<option value={model.id} class="bg-gray-50 dark:bg-gray-700" />
											{/each}
										</datalist>
									</div>
								</div>
							</div>
						</div>

						<div class="mt-3 space-y-3">
							<div>
								<div class="mb-1.5 text-xs font-medium">{$i18n.t('Runtime Tuning')}</div>
								<div class="grid grid-cols-2 gap-2">
									<input
										class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
										bind:value={omniVoiceDeviceMap}
										placeholder="cuda:0"
										on:input={syncTTSParamsFromOmniVoiceEditor}
									/>
									<input
										class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
										bind:value={omniVoiceDtype}
										placeholder="float16"
										on:input={syncTTSParamsFromOmniVoiceEditor}
									/>
									<input
										class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
										bind:value={omniVoiceAttnImplementation}
										placeholder={$i18n.t('Attention impl (optional)')}
										on:input={syncTTSParamsFromOmniVoiceEditor}
									/>
									<input
										class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
										bind:value={omniVoiceSpeed}
										placeholder={$i18n.t('Default speed')}
										on:input={syncTTSParamsFromOmniVoiceEditor}
									/>
									<input
										class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
										bind:value={omniVoiceNumStep}
										placeholder={$i18n.t('Default num_step')}
										on:input={syncTTSParamsFromOmniVoiceEditor}
									/>
									<input
										class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
										bind:value={omniVoiceDuration}
										placeholder={$i18n.t('Default duration')}
										on:input={syncTTSParamsFromOmniVoiceEditor}
									/>
								</div>
							</div>

							<div>
								<div class="mb-1.5 text-xs font-medium">{$i18n.t('Voice Preset')}</div>
								<div class="flex gap-2">
									<input
										list="omnivoice-preset-list"
										class="flex-1 rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
										bind:value={omniVoicePresetId}
										placeholder={$i18n.t('Preset ID')}
										on:change={() => {
											if (omniVoiceVoices[omniVoicePresetId]) {
												loadOmniVoicePresetDraft(omniVoicePresetId);
												TTS_VOICE = omniVoicePresetId;
											}
										}}
									/>
									<datalist id="omnivoice-preset-list">
										{#each Object.entries(omniVoiceVoices) as [presetId, preset]}
											<option value={presetId}>{preset?.name ?? titleCaseId(presetId)}</option>
										{/each}
									</datalist>

									<button
										class="px-2.5 bg-gray-50 hover:bg-gray-200 text-gray-800 dark:bg-gray-850 dark:hover:bg-gray-800 dark:text-gray-100 rounded-lg transition"
										type="button"
										on:click={newOmniVoicePreset}
									>
										{$i18n.t('New')}
									</button>
									<button
										class="px-2.5 bg-gray-50 hover:bg-gray-200 text-gray-800 dark:bg-gray-850 dark:hover:bg-gray-800 dark:text-gray-100 rounded-lg transition"
										type="button"
										on:click={saveOmniVoicePreset}
									>
										{$i18n.t('Save Preset')}
									</button>
									<button
										class="px-2.5 bg-gray-50 hover:bg-gray-200 text-gray-800 dark:bg-gray-850 dark:hover:bg-gray-800 dark:text-gray-100 rounded-lg transition"
										type="button"
										on:click={deleteOmniVoicePreset}
										disabled={!omniVoiceVoices[omniVoicePresetId]}
									>
										{$i18n.t('Delete')}
									</button>
								</div>
								<div class="mt-2 grid grid-cols-2 gap-2">
									<input
										class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
										bind:value={omniVoicePresetName}
										placeholder={$i18n.t('Preset name')}
									/>
									<div class="flex gap-2">
										<input
											class="flex-1 rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
											value={omniVoicePresetRefAudioFileId
												? omniVoicePresetRefAudioFilename || omniVoicePresetRefAudioFileId
												: omniVoicePresetRefAudio}
											placeholder={$i18n.t('Upload reference audio')}
											on:input={(event) => {
												const value = (event.currentTarget as HTMLInputElement).value;
												omniVoicePresetRefAudio = value;
												omniVoicePresetRefAudioFileId = '';
												omniVoicePresetRefAudioFilename = '';
											}}
										/>
										<input
											class="hidden"
											type="file"
											accept="audio/*,.wav,.mp3,.m4a,.flac,.ogg,.webm"
											bind:this={omniVoiceRefAudioInputElement}
											on:change={async (event) => {
												const input = event.currentTarget as HTMLInputElement;
												const file = input.files?.[0] ?? null;
												await uploadOmniVoiceRefAudio(file);
											}}
										/>
										<button
											class="px-2.5 bg-gray-50 hover:bg-gray-200 text-gray-800 dark:bg-gray-850 dark:hover:bg-gray-800 dark:text-gray-100 rounded-lg transition disabled:opacity-60"
											type="button"
											on:click={triggerOmniVoiceRefAudioUpload}
											disabled={omniVoiceRefAudioUploadLoading}
										>
											{#if omniVoiceRefAudioUploadLoading}
												{$i18n.t('Uploading')}
											{:else}
												{$i18n.t('Upload')}
											{/if}
										</button>
										<button
											class="px-2.5 bg-gray-50 hover:bg-gray-200 text-gray-800 dark:bg-gray-850 dark:hover:bg-gray-800 dark:text-gray-100 rounded-lg transition disabled:opacity-60"
											type="button"
											on:click={clearOmniVoiceRefAudio}
											disabled={
												!omniVoicePresetRefAudio &&
												!omniVoicePresetRefAudioFileId &&
												!omniVoicePresetRefAudioFilename
											}
										>
											{$i18n.t('Clear')}
										</button>
									</div>
								</div>
								<div class="mt-2 text-xs text-gray-400 dark:text-gray-500">
									{#if omniVoicePresetRefAudioFileId}
										{$i18n.t('Uploaded reference audio will be stored by file ID and reused for this preset.')}
									{:else}
										{$i18n.t('You can upload a short sample or paste an existing local path.')}
									{/if}
								</div>
								<div class="mt-2">
									<Textarea
										className="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
										bind:value={omniVoicePresetInstruct}
										placeholder={$i18n.t('Free-form voice instructions')}
										minSize={90}
									/>
								</div>
								<div class="mt-2">
									<Textarea
										className="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
										bind:value={omniVoicePresetRefText}
										placeholder={$i18n.t('Reference transcript for cloned voices')}
										minSize={90}
									/>
								</div>
								<div class="mt-2 grid grid-cols-3 gap-2">
									<input
										class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
										bind:value={omniVoicePresetSpeed}
										placeholder={$i18n.t('Preset speed')}
									/>
									<input
										class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
										bind:value={omniVoicePresetNumStep}
										placeholder={$i18n.t('Preset num_step')}
									/>
									<input
										class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
										bind:value={omniVoicePresetDuration}
										placeholder={$i18n.t('Preset duration')}
									/>
								</div>
							</div>

							<div>
								<div class="mb-1.5 text-xs font-medium">{$i18n.t('Preview Text')}</div>
								<Textarea
									className="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
									bind:value={omniVoicePreviewText}
									placeholder={$i18n.t('Enter a sample text to preview this preset')}
									minSize={110}
								/>
								<div class="mt-2 flex items-center gap-2">
									<button
										class="px-2.5 py-2 bg-gray-50 hover:bg-gray-200 text-gray-800 dark:bg-gray-850 dark:hover:bg-gray-800 dark:text-gray-100 rounded-lg transition disabled:opacity-60"
										type="button"
										on:click={previewOmniVoiceHandler}
										disabled={omniVoicePreviewLoading}
									>
										{#if omniVoicePreviewLoading}
											{$i18n.t('Saving & Previewing')}
										{:else}
											{$i18n.t('Save & Preview')}
										{/if}
									</button>
									<div class="text-xs text-gray-400 dark:text-gray-500">
										{$i18n.t('Preview uses the saved backend settings, not unsaved draft fields.')}
									</div>
								</div>
							</div>

							<div>
								<div class="mb-1.5 text-xs font-medium">
									{$i18n.t('Advanced JSON Overrides')}
								</div>
								<Textarea
									className="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 font-mono dark:text-gray-300 dark:bg-gray-850 outline-hidden"
									bind:value={omniVoiceExtraParams}
									placeholder={$i18n.t('Optional JSON object for OmniVoice parameters not exposed above')}
									minSize={120}
								/>
								<div class="mt-2 text-xs text-gray-400 dark:text-gray-500">
									{$i18n.t(
										'Use this for unsupported OmniVoice options. Exposed fields above win if the same key appears in both places.'
									)}
								</div>
							</div>

							<div class="text-xs text-gray-400 dark:text-gray-500">
								{$i18n.t(
									'OmniVoice tuning in this panel is inference-time only. Use presets to store `instruct`, reference audio/text, and per-preset overrides; use Save & Preview to hear the saved result immediately.'
								)}
							</div>
						</div>
					{:else if TTS_ENGINE === 'elevenlabs'}
						<div class=" flex gap-2">
							<div class="w-full">
								<div class=" mb-1.5 text-xs font-medium">{$i18n.t('TTS Voice')}</div>
								<div class="flex w-full">
									<div class="flex-1">
										<input
											list="voice-list"
											class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
											bind:value={TTS_VOICE}
											placeholder={$i18n.t('Select a voice')}
										/>

										<datalist id="voice-list">
											{#each voices as voice}
												<option value={voice.id}>{voice.name}</option>
											{/each}
										</datalist>
									</div>
								</div>
							</div>
							<div class="w-full">
								<div class=" mb-1.5 text-xs font-medium">{$i18n.t('TTS Model')}</div>
								<div class="flex w-full">
									<div class="flex-1">
										<input
											list="tts-model-list"
											class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
											bind:value={TTS_MODEL}
											placeholder={$i18n.t('Select a model')}
										/>

										<datalist id="tts-model-list">
											{#each models as model}
												<option value={model.id} class="bg-gray-50 dark:bg-gray-700" />
											{/each}
										</datalist>
									</div>
								</div>
							</div>
						</div>
					{:else if TTS_ENGINE === 'azure'}
						<div class=" flex gap-2">
							<div class="w-full">
								<div class=" mb-1.5 text-xs font-medium">{$i18n.t('TTS Voice')}</div>
								<div class="flex w-full">
									<div class="flex-1">
										<input
											list="voice-list"
											class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
											bind:value={TTS_VOICE}
											placeholder={$i18n.t('Select a voice')}
										/>

										<datalist id="voice-list">
											{#each voices as voice}
												<option value={voice.id}>{voice.name}</option>
											{/each}
										</datalist>
									</div>
								</div>
							</div>
							<div class="w-full">
								<div class=" mb-1.5 text-xs font-medium">
									{$i18n.t('Output format')}
									<a
										href="https://learn.microsoft.com/en-us/azure/ai-services/speech-service/rest-text-to-speech?tabs=streaming#audio-outputs"
										target="_blank"
									>
										<small>{$i18n.t('Available list')}</small>
									</a>
								</div>
								<div class="flex w-full">
									<div class="flex-1">
										<input
											list="tts-model-list"
											class="w-full rounded-lg py-2 px-4 text-sm bg-gray-50 dark:text-gray-300 dark:bg-gray-850 outline-hidden"
											bind:value={TTS_AZURE_SPEECH_OUTPUT_FORMAT}
											placeholder={$i18n.t('Select an output format')}
										/>
									</div>
								</div>
							</div>
						</div>
					{/if}
				</div>

				<div class="pt-0.5 flex w-full justify-between">
					<div class="self-center text-xs font-medium">{$i18n.t('Response splitting')}</div>
					<div class="flex items-center relative">
						<select
							class="w-fit pr-8 cursor-pointer rounded-sm px-2 p-1 text-xs bg-transparent outline-hidden text-right"
							aria-label={$i18n.t('Select how to split message text for TTS requests')}
							bind:value={TTS_SPLIT_ON}
						>
							{#each Object.values(TTS_RESPONSE_SPLIT) as split}
								<option value={split}
									>{$i18n.t(split.charAt(0).toUpperCase() + split.slice(1))}</option
								>
							{/each}
						</select>
					</div>
				</div>
				<div class="mt-2 mb-1 text-xs text-gray-400 dark:text-gray-500">
					{$i18n.t(
						"Control how message text is split for TTS requests. 'Punctuation' splits into sentences, 'paragraphs' splits into paragraphs, and 'none' keeps the message as a single string."
					)}
				</div>
			</div>
		</div>
	</div>
	<div class="flex justify-end text-sm font-medium">
		<button
			class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full"
			type="submit"
		>
			{$i18n.t('Save')}
		</button>
	</div>
</form>

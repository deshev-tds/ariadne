import { env } from '@huggingface/transformers';
import { KokoroTTS } from 'kokoro-js';

// TODO: Below doesn't work as expected, need to investigate further
env.backends.onnx.wasm.wasmPaths = '/wasm/';

let tts;
let isInitialized = false; // Flag to track initialization status
const DEFAULT_MODEL_ID = 'onnx-community/Kokoro-82M-v1.0-ONNX'; // Default model

self.onmessage = async (event) => {
	const { type, payload } = event.data;

	if (type === 'init') {
		let { model_id, dtype, preferWebGPU } = payload;
		model_id = model_id || DEFAULT_MODEL_ID; // Use default model if none provided
		dtype = typeof dtype === 'string' ? dtype : 'fp32';

		self.postMessage({ status: 'init:start' });

		const devices =
			preferWebGPU !== false && !!navigator?.gpu ? ['webgpu', 'wasm'] : ['wasm'];
		let lastError: unknown = null;

		try {
			for (const device of devices) {
				try {
					tts = await KokoroTTS.from_pretrained(model_id, {
						dtype,
						device
					});
					isInitialized = true; // Mark as initialized after successful loading
					self.postMessage({ status: 'init:complete', device });
					return;
				} catch (error) {
					lastError = error;
					console.warn(`Kokoro worker init failed on ${device}, trying fallback`, error);
				}
			}
		} catch (_error) {
			// Fall through to unified error handling below.
		}

		if (!isInitialized) {
			isInitialized = false; // Ensure it's marked as false on failure
			self.postMessage({
				status: 'init:error',
				error: lastError instanceof Error ? lastError.message : String(lastError)
			});
		}
	}

	if (type === 'generate') {
		if (!isInitialized || !tts) {
			// Ensure model is initialized
			self.postMessage({ status: 'generate:error', error: 'TTS model not initialized' });
			return;
		}

		const { text, voice } = payload;
		self.postMessage({ status: 'generate:start' });

		try {
			const rawAudio = await tts.generate(text, { voice });
			const blob = await rawAudio.toBlob();
			const blobUrl = URL.createObjectURL(blob);
			self.postMessage({ status: 'generate:complete', audioUrl: blobUrl });
		} catch (error) {
			self.postMessage({ status: 'generate:error', error: error.message });
		}
	}

	if (type === 'status') {
		// Respond with the current initialization status
		self.postMessage({ status: 'status:check', initialized: isInitialized });
	}
};

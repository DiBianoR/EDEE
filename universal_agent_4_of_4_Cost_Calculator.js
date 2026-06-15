// use with usageMetadata & groundingMetadata to calculate costs
const cost_registry = {
  "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-pro-preview:generateContent": {
    input: 2.00,
    output_text: 12.00,
    contextThreshold: 200000,
    input_over_threshold: 4.00,
    output_text_over_threshold: 18.00,
    cache_read: 0.20,
    cache_read_over_threshold: 0.40,
    cache_storage_hourly: 4.50,
    grounding_search_per_1k: 14.00,  // A customer-submitted request to Gemini may result in one or more queries to Google Search. You will be charged for each individual search query performed.
    grounding_maps_per_1k: 14.00
  },
  "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash:generateContent": {
    input: 1.50,
    output_text: 9.00,
    cache_read: 0.15,
    cache_storage_hourly: 1.00,
    grounding_search_per_1k: 14.00,  // A customer-submitted request to Gemini may result in one or more queries to Google Search. You will be charged for each individual search query performed.
    grounding_maps_per_1k: 14.00
  },
  "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent": {
    input_default: 0.25, // text, image, video
    input_audio: 0.50,   // double rate for audio
    output_text: 1.50,
    cache_read_default: 0.025,
    cache_read_audio: 0.05,
    cache_storage_hourly: 1.00,
    grounding_search_per_1k: 14.00,
    grounding_maps_per_1k: 14.00
  },
  "https://generativelanguage.googleapis.com/v1beta/models/gemini-3-pro-image:generateContent": {
    // Inherits base text/image properties from Gemini 3.1 Pro
    input: 2.00,
    output_text: 12.00,
    output_image: 120.00,
    grounding_search_per_1k: 14.00
  },
  "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-image:generateContent": {
    input: 0.50,
    output_text: 3.00,
    output_image: 60.00,
    grounding_search_per_1k: 14.00
  },
  "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-image:generateContent": {
    input: 0.30,
    output_text: 2.50,   // Matched to base 2.5 Flash text output rate
    output_image: 30.00
  }
};

// --- EXECUTION ENGINE ---
const payload = $input.item.json;

// 1. Isolate the core data from the transient metadata
const outputData = payload.outputData;
const modelUrl = payload.model_url;
const usage = payload.usage || {};
const grounding = payload.grounding || {};

let taskCost = 0;
const pricing = cost_registry[modelUrl];

if (pricing) {
    const totalPromptTokens = usage.promptTokenCount ?? 0;
    const totalTokens = usage.totalTokenCount ?? 0;

    // Evaluate Context Window Threshold
    const threshold = pricing.contextThreshold ?? 0;
    const isOverThreshold = threshold > 0 && totalTokens > threshold;

    // Calculate Input Tokens
    let inputCost = 0;
    const promptDetails = usage.promptTokensDetails || [{ modality: "TEXT", tokenCount: totalPromptTokens }];

    for (const detail of promptDetails) {
        const modality = (detail.modality ?? "TEXT").toLowerCase();
        const tokenCount = detail.tokenCount ?? 0;
        if (tokenCount === 0) continue;

        let baseInputRate = null;
        if (pricing.input !== undefined) {
            baseInputRate = pricing.input;
        } else if (pricing[`input_${modality}`] !== undefined) {
            baseInputRate = pricing[`input_${modality}`];
        } else if (pricing.input_default !== undefined) {
            baseInputRate = pricing.input_default;
        } else {
            throw new Error(`Billing Error: No input rate found for model ${modelUrl} (Modality: ${modality})`);
        }

        if (isOverThreshold) {
            baseInputRate = pricing.input_over_threshold ?? (baseInputRate * 2);
        }
        inputCost += (tokenCount / 1000000) * baseInputRate;
    }

    // Calculate Output Tokens
    let outputCost = 0;
    const candidateDetails = usage.candidatesTokensDetails || [{ modality: "TEXT", tokenCount: usage.candidatesTokenCount ?? 0 }];

    for (const detail of candidateDetails) {
        const modality = (detail.modality ?? "TEXT").toLowerCase();
        const tokenCount = detail.tokenCount ?? 0;
        if (tokenCount === 0) continue;

        let baseOutputRate = 0;
        if (modality === "text") {
            baseOutputRate = pricing.output_text ?? 0;
            if (isOverThreshold) {
                baseOutputRate = pricing.output_text_over_threshold ?? (baseOutputRate * 2);
            }
        } else if (modality === "image") {
            baseOutputRate = pricing.output_image ?? 0;
        } else {
            baseOutputRate = pricing[`output_${modality}`] ?? pricing.output_text ?? 0;
        }

        outputCost += (tokenCount / 1000000) * baseOutputRate;
    }

    // Calculate Grounding Search Fees
    let groundingCost = 0;
    if (grounding.webSearchQueries && Array.isArray(grounding.webSearchQueries)) {
        const searchQueriesCount = grounding.webSearchQueries.length;
        const searchRatePer1k = pricing.grounding_search_per_1k ?? 0;
        groundingCost = (searchQueriesCount / 1000) * searchRatePer1k;
    }

    taskCost = inputCost + outputCost + groundingCost;
}

// 2. Inject ONLY the final cost into the most recent stepRecord
if (outputData.history && outputData.history.length > 0) {
    outputData.history[outputData.history.length - 1].cost = Number(taskCost.toFixed(6));
}

// 3. Return ONLY the outputData, cleanly stripping all billing metadata
return { json: outputData };
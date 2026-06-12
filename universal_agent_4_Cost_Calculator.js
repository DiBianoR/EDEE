// --- EXECUTION ENGINE ---
const payload = $input.item.json;

// 1. Isolate the core data from the transient metadata
const outputData = payload.outputData;
const modelUrl = payload.model_url;
const usage = payload.usage || {};
const grounding = payload.grounding || {};

// HOIST: Grab the cost registry directly from the config passed by Node 3
const cost_registry = outputData.config?.cost_registry;

if (!cost_registry) {
    throw new Error("Billing Error: 'cost_registry' is missing from the global config.");
}

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
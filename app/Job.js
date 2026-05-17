const mongoose = require('mongoose');

const inputInfoSchema = new mongoose.Schema({
    bucket: String,
    key: String,
    sizeBytes: Number
}, { _id: false });

const costInfoSchema = new mongoose.Schema({
    totalEstimated: Number,
    currentSpent: Number,
    currency: String
}, { _id: false });

const translatedInfoSchema = new mongoose.Schema({
    markdownKey: String,
    chunkCount: Number,
    completedChunks: Number
}, { _id: false });

const outputInfoSchema = new mongoose.Schema({
    pdfKey: String,
    sizeBytes: Number
}, { _id: false });

const jobSchema = new mongoose.Schema({
    _id: String,
    tenantId: String,
    sourceLanguage: String,
    targetLanguage: String,
    filename: String,
    status: {
        type: String,
        enum: ['PENDING', 'CONVERTING', 'CONVERTED', 'TRANSLATING', 'TRANSLATED', 'RENDERING', 'COMPLETED', 'FAILED', 'CANCELLED'],
        default: 'PENDING'
    },
    createdAt: { type: Date, default: Date.now },
    updatedAt: { type: Date, default: Date.now },
    input: inputInfoSchema,
    costs: costInfoSchema,
    translated: translatedInfoSchema,
    output: outputInfoSchema,
    processingLog: [String],
    failureReason: String,
    options: {
        style: String,
        tone: String,
        docType: String,
        formatting: String,
        keepTerms: Boolean,
        customPrompt: String
    }
}, {
    collection: 'translation_jobs',
    timestamps: false
});

module.exports = mongoose.model('Job', jobSchema);

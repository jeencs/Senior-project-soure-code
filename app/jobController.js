const Job = require('./Job');
const Minio = require('minio');
const { v4: uuidv4 } = require('uuid');
const axios = require('axios');

/**
 * برای ارتباط برقرار شدن مین آینو این ساخته شده 
 */
const minioClient = new Minio.Client({
    endPoint: process.env.MINIO_ENDPOINT || 'localhost',
    port: parseInt(process.env.MINIO_PORT) || 9000,
    useSSL: process.env.MINIO_USE_SSL === 'true',
    accessKey: process.env.MINIO_ACCESS_KEY || 'minioadmin',
    secretKey: process.env.MINIO_SECRET_KEY || 'minioadmin'
});

// این متغییر برای ارتباط براقرار کردن سرویس های  کانورتر، اورکیستریتر و ریندرد
const CONVERTER_URL = process.env.CONVERTER_SERVICE_URL;
const ORCHESTRATOR_URL = process.env.ORCHESTRATOR_SERVICE_URL;
const RENDERER_URL = process.env.RENDERER_SERVICE_URL;

// کار این بخش این است که یک وظیفه جدید برای شروع پروسه تبدیل کردن پی دی اف استفاده میشود
exports.createJob = async (req, res) => {
    try {
        const { tenantId, sourceLang, targetLang } = req.body;

        let options = {};

        if (req.body.options) {
            try {
                options = JSON.parse(req.body.options);
            } catch (e) {
                console.warn('مشکل اینجاد شده در بخش پارس کردن', e);
            }
        }
        const file = req.file;

        if (!file) {
            return res.status(400).json({ error: 'No file uploaded' });
        }

        const jobId = uuidv4();
        const filename = file.originalname;
        const bucket = 'documents';
        const key = `${tenantId}/${jobId}/source/${filename}`;

        console.log(`Uploading ${filename} to MinIO bucket ${bucket} with key ${key}`);

        await minioClient.putObject(bucket, key, file.buffer, file.size, {
            'Content-Type': file.mimetype
        });

        const job = new Job({
            _id: jobId,
            tenantId,
            sourceLanguage: sourceLang,
            targetLanguage: targetLang,
            filename,
            status: 'PENDING',
            createdAt: new Date(),
            updatedAt: new Date(),
            input: {
                bucket,
                key,
                sizeBytes: file.size
            },
            costs: {
                totalEstimated: 0.0,
                currentSpent: 0.0,
                currency: 'USD'
            },
            processingLog: [`Job created at ${new Date().toISOString()}`],
            options
        });

        await job.save();

        runTranslationFlow(jobId).catch(err => {
            console.error(`Flow failed for job ${jobId}:`, err);
        });

        res.status(201).json(job);
    } catch (error) {
        console.error('Error creating job:', error);
        res.status(500).json({ error: error.message });
    }
};

async function runTranslationFlow(jobId) {
    try {
        console.log(`Starting translation flow for job ${jobId}`);

        console.log(`Calling converter for job ${jobId}`);
        await axios.post(`${CONVERTER_URL}/convert`, { jobId });

        console.log(`Calling orchestrator for job ${jobId}`);
        await axios.post(`${ORCHESTRATOR_URL}/translate`, { jobId });

        console.log(`Calling renderer for job ${jobId}`);
        await axios.post(`${RENDERER_URL}/render`, { jobId });

        console.log(`Translation flow completed for job ${jobId}`);
    } catch (error) {
        console.error(`Error in translation flow for job ${jobId}:`, error.message);
        await Job.findByIdAndUpdate(jobId, {
            status: 'FAILED',
            updatedAt: new Date(),
            failureReason: error.message,
            $push: { processingLog: `Error: ${error.message}` }
        });
    }
}

exports.getJob = async (req, res) => {
    try {
        const job = await Job.findById(req.params.jobId);
        if (!job) {
            return res.status(404).json({ error: 'Job not found' });
        }
        res.json(job);
    } catch (error) {
        res.status(500).json({ error: error.message });
    }
};

exports.cancelJob = async (req, res) => {
    try {
        const job = await Job.findById(req.params.jobId);
        if (!job) {
            return res.status(404).json({ error: 'Job not found' });
        }

        const nonCancellable = ['TRANSLATING', 'TRANSLATED', 'RENDERING', 'COMPLETED'];
        if (nonCancellable.includes(job.status)) {
            return res.status(400).json({ error: `Job cannot be cancelled in status: ${job.status}` });
        }

        job.status = 'CANCELLED';
        job.updatedAt = new Date();
        job.processingLog.push(`Job cancelled at ${new Date().toISOString()}`);
        await job.save();

        res.json(job);
    } catch (error) {
        res.status(500).json({ error: error.message });
    }
};

exports.listJobs = async (req, res) => {
    try {
        const jobs = await Job.find().sort({ createdAt: -1 });
        res.json(jobs);
    } catch (error) {
        res.status(500).json({ error: error.message });
    }
};

exports.downloadJobResult = async (req, res) => {
    try {
        console.log(`Download request for job: ${req.params.jobId}`);
        const job = await Job.findById(req.params.jobId);
        if (!job) {
            console.log(`Job not found: ${req.params.jobId}`);
            return res.status(404).json({ error: 'Job not found' });
        }
        console.log(`Job status: ${job.status}, PDF Key: ${job.output?.pdfKey}`);
        if (job.status !== 'COMPLETED' || !job.output?.pdfKey) {
            return res.status(404).json({ error: 'Result not ready or not found' });
        }

        const bucket = job.input.bucket || 'documents';
        const stream = await minioClient.getObject(bucket, job.output.pdfKey);

        res.setHeader('Content-Type', 'application/pdf');
        res.setHeader('Content-Disposition', `attachment; filename="translated_${job.filename}"`);

        stream.pipe(res);
    } catch (error) {
        res.status(500).json({ error: error.message });
    }
};

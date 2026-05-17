const express = require('express');
const router = express.Router();
const multer = require('multer');
const jobController = require('./jobController');

const storage = multer.memoryStorage();
const upload = multer({ storage: storage });

router.post('/', upload.single('pdf'), jobController.createJob);
router.get('/', jobController.listJobs);
router.get('/:jobId', jobController.getJob);
router.get('/:jobId/download', jobController.downloadJobResult);
router.post('/:jobId/cancel', jobController.cancelJob);

module.exports = router;

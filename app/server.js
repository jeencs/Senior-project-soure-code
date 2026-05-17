require('dotenv').config();
const express = require('express');
const mongoose = require('mongoose');
const cors = require('cors');
const jobRoutes = require('./jobRoutes');

const app = express();
const PORT = process.env.PORT || 8080;

app.use(cors());
app.use(express.json());
app.use(express.static('public'));

app.use('/api/jobs', jobRoutes);

app.get('/health', (req, res) => {
    res.json({ status: 'healthy', service: 'app' });
});

mongoose.connect(process.env.MONGO_URI)
    .then(() => {
        console.log('مانگو دی بی کنیک شده');
        app.listen(PORT, () => {
            console.log(`سرور روی پورت ${PORT} ران شده`);
        });
    })
    .catch(err => {
        console.error('مانگو دی بی کنکشن مشکل دارد:', err);
    });

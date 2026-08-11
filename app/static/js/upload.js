// Возобновляемая загрузка образа.
// Файл режется на куски и досылается с указанием смещения. Сервер принимает
// кусок, только если смещение совпало с реальным размером недокачанного файла,
// а при расхождении сообщает верное — так обрыв на 90% не начинает всё заново.

(function () {
    const CHUNK = 8 * 1024 * 1024;

    const fileInput = document.getElementById('file');
    const button = document.getElementById('upload');
    const progress = document.getElementById('progress');
    if (!fileInput || !button) return;

    fileInput.addEventListener('change', () => {
        button.disabled = !fileInput.files.length;
        progress.textContent = '';
    });

    button.addEventListener('click', async () => {
        const file = fileInput.files[0];
        if (!file) return;

        button.disabled = true;
        try {
            const started = await post('/library/upload/init', {
                filename: file.name,
                size: file.size,
            });
            if (started.error) throw new Error(started.error);

            let offset = started.offset || 0;
            while (offset < file.size) {
                const slice = file.slice(offset, offset + CHUNK);
                const response = await fetch('/library/upload/' + started.upload_id, {
                    method: 'POST',
                    headers: {
                        'X-Upload-Offset': String(offset),
                        'X-CSRF-Token': window.CSRF_TOKEN,
                        'Content-Type': 'application/octet-stream',
                    },
                    body: slice,
                });
                const data = await response.json();

                if (response.status === 409) {
                    // Сервер знает лучше — продолжаем с его смещения.
                    offset = data.offset;
                    continue;
                }
                if (!response.ok) throw new Error(data.error || 'ошибка загрузки');

                offset = data.offset;
                show(offset, file.size);
            }

            const done = await post('/library/upload/' + started.upload_id + '/finish', {
                filename: file.name,
            });
            if (done.error) throw new Error(done.error);

            progress.textContent = 'Готово, обновляю список...';
            location.reload();
        } catch (error) {
            progress.textContent = 'Ошибка: ' + error.message;
            button.disabled = false;
        }
    });

    function show(done, total) {
        const percent = Math.floor((done * 100) / total);
        progress.textContent = percent + '% (' + (done / 1e9).toFixed(2) + ' из ' +
            (total / 1e9).toFixed(2) + ' ГБ)';
    }

    async function post(url, payload) {
        const response = await fetch(url, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRF-Token': window.CSRF_TOKEN,
            },
            body: JSON.stringify(payload),
        });
        return response.json();
    }
})();

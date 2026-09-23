/**
 * StuLink v1.9.2 2026-09-18
 * 表单收集系统 - 可视化题目编辑器（form_create 页面）
 * Copyright (c) 2026 zkxxzf. Apache License 2.0
 */
(function () {
    'use strict';

    const config = window.FORM_CONFIG || {};
    const questionTypes = config.questionTypes || [];
    const container = document.getElementById('questionsContainer');
    const emptyHint = document.getElementById('emptyHint');
    const addBtn = document.getElementById('addQuestionBtn');
    const hiddenInput = document.getElementById('questions_json');
    const form = document.getElementById('formBuilderForm');

    let questions = [];
    let questionIdCounter = 0;

    // ── 初始化 ─────────────────────────────────────────────────

    function init() {
        // 编辑模式：加载已有数据
        if (config.editMode && config.formData && config.formData.questions) {
            questions = config.formData.questions.map(function (q) {
                return {
                    _id: questionIdCounter++,
                    question_type: q.question_type || 'text',
                    title: q.title || '',
                    description: q.description || '',
                    options: q.options || [],
                    required: !!q.required,
                    file_types: q.file_types || '',
                    max_file_size_mb: q.max_file_size_mb || '',
                };
            });
        }
        render();
        addBtn.addEventListener('click', function () { addQuestion(); });
        form.addEventListener('submit', function () { collectAndSubmit(); });
    }

    // ── 渲染 ───────────────────────────────────────────────────

    function render() {
        // 清空（保留 emptyHint）
        container.innerHTML = '';
        container.appendChild(emptyHint);
        emptyHint.style.display = questions.length === 0 ? '' : 'none';

        questions.forEach(function (q, idx) {
            const card = buildQuestionCard(q, idx);
            container.appendChild(card);
        });
    }

    function buildQuestionCard(q, idx) {
        const div = document.createElement('div');
        div.className = 'card mb-2 border';
        div.dataset.qid = q._id;

        const typeOptions = questionTypes.map(function (t) {
            const selected = q.question_type === t[0] ? 'selected' : '';
            return `<option value="${t[0]}" ${selected}>${t[1]}</option>`;
        }).join('');

        const isChoice = q.question_type === 'single_choice' || q.question_type === 'multi_choice';
        const isFile = q.question_type === 'file';

        let optionsHtml = '';
        if (isChoice) {
            const opts = (q.options || []).map(function (opt, oi) {
                return `
                    <div class="input-group input-group-sm mb-1 option-row">
                        <input type="text" class="form-control opt-input" value="${escapeAttr(opt)}" placeholder="选项 ${oi + 1}">
                        <button type="button" class="btn btn-outline-danger btn-remove-opt" title="删除选项">
                            <i class="bi bi-x"></i>
                        </button>
                    </div>`;
            }).join('');
            optionsHtml = `
                <div class="mt-2 options-container">
                    <label class="form-label small fw-bold">选项列表</label>
                    ${opts}
                    <button type="button" class="btn btn-sm btn-outline-primary btn-add-opt mt-1">
                        <i class="bi bi-plus"></i> 添加选项
                    </button>
                </div>`;
        }

        let fileHtml = '';
        if (isFile) {
            fileHtml = `
                <div class="row mt-2">
                    <div class="col-6">
                        <label class="form-label small">允许文件类型（逗号分隔）</label>
                        <input type="text" class="form-control form-control-sm file-types-input"
                               value="${escapeAttr(q.file_types)}" placeholder="pdf,doc,docx">
                    </div>
                    <div class="col-6">
                        <label class="form-label small">单文件大小限制 (MB)</label>
                        <input type="number" class="form-control form-control-sm file-size-input"
                               value="${q.max_file_size_mb}" min="1" max="100" placeholder="10">
                    </div>
                </div>`;
        }

        div.innerHTML = `
            <div class="card-body py-2">
                <div class="d-flex justify-content-between align-items-start mb-2">
                    <span class="badge bg-secondary">题目 ${idx + 1}</span>
                    <div class="d-flex gap-1">
                        <button type="button" class="btn btn-sm btn-outline-secondary btn-move-up" title="上移" ${idx === 0 ? 'disabled' : ''}>
                            <i class="bi bi-arrow-up"></i>
                        </button>
                        <button type="button" class="btn btn-sm btn-outline-secondary btn-move-down" title="下移" ${idx === questions.length - 1 ? 'disabled' : ''}>
                            <i class="bi bi-arrow-down"></i>
                        </button>
                        <button type="button" class="btn btn-sm btn-outline-danger btn-delete-q" title="删除题目">
                            <i class="bi bi-trash"></i>
                        </button>
                    </div>
                </div>
                <div class="row g-2">
                    <div class="col-md-3">
                        <label class="form-label small">题型</label>
                        <select class="form-select form-select-sm q-type-select">${typeOptions}</select>
                    </div>
                    <div class="col-md-6">
                        <label class="form-label small">题目标题 <span class="text-danger">*</span></label>
                        <input type="text" class="form-control form-control-sm q-title" value="${escapeAttr(q.title)}" placeholder="请输入题目" required>
                    </div>
                    <div class="col-md-3">
                        <label class="form-label small">必填</label>
                        <div class="form-check form-switch mt-1">
                            <input class="form-check-input q-required-check" type="checkbox" ${q.required ? 'checked' : ''}>
                        </div>
                    </div>
                </div>
                <div class="mt-2">
                    <label class="form-label small">说明（可选）</label>
                    <input type="text" class="form-control form-control-sm q-description" value="${escapeAttr(q.description)}" placeholder="补充说明">
                </div>
                ${optionsHtml}
                ${fileHtml}
            </div>`;

        // 绑定事件
        bindCardEvents(div, q);
        return div;
    }

    function bindCardEvents(card, q) {
        // 题型切换
        card.querySelector('.q-type-select').addEventListener('change', function () {
            q.question_type = this.value;
            render();
        });

        // 标题
        card.querySelector('.q-title').addEventListener('input', function () {
            q.title = this.value;
        });

        // 说明
        card.querySelector('.q-description').addEventListener('input', function () {
            q.description = this.value;
        });

        // 必填
        card.querySelector('.q-required-check').addEventListener('change', function () {
            q.required = this.checked;
        });

        // 上移/下移
        const upBtn = card.querySelector('.btn-move-up');
        const downBtn = card.querySelector('.btn-move-down');
        const idx = questions.indexOf(q);

        if (upBtn) upBtn.addEventListener('click', function () {
            if (idx > 0) {
                questions.splice(idx, 1);
                questions.splice(idx - 1, 0, q);
                render();
            }
        });
        if (downBtn) downBtn.addEventListener('click', function () {
            if (idx < questions.length - 1) {
                questions.splice(idx, 1);
                questions.splice(idx + 1, 0, q);
                render();
            }
        });

        // 删除题目
        card.querySelector('.btn-delete-q').addEventListener('click', function () {
            if (confirm('确定删除此题目？')) {
                questions.splice(questions.indexOf(q), 1);
                render();
            }
        });

        // 选择题：添加选项
        const addOptBtn = card.querySelector('.btn-add-opt');
        if (addOptBtn) {
            addOptBtn.addEventListener('click', function () {
                if (!q.options) q.options = [];
                q.options.push('新选项');
                render();
            });
        }

        // 选择题：删除选项
        card.querySelectorAll('.btn-remove-opt').forEach(function (btn, oi) {
            btn.addEventListener('click', function () {
                q.options.splice(oi, 1);
                render();
            });
        });

        // 选择题：选项内容修改
        card.querySelectorAll('.opt-input').forEach(function (input, oi) {
            input.addEventListener('input', function () {
                q.options[oi] = this.value;
            });
        });

        // 文件题：文件类型和大小
        const fileTypesInput = card.querySelector('.file-types-input');
        if (fileTypesInput) {
            fileTypesInput.addEventListener('input', function () {
                q.file_types = this.value;
            });
        }
        const fileSizeInput = card.querySelector('.file-size-input');
        if (fileSizeInput) {
            fileSizeInput.addEventListener('input', function () {
                q.max_file_size_mb = this.value;
            });
        }
    }

    // ── 添加题目 ───────────────────────────────────────────────

    function addQuestion() {
        questions.push({
            _id: questionIdCounter++,
            question_type: 'text',
            title: '',
            description: '',
            options: [],
            required: false,
            file_types: '',
            max_file_size_mb: '',
        });
        render();
    }

    // ── 收集并提交 ─────────────────────────────────────────────

    function collectAndSubmit() {
        // 验证所有题目有标题
        for (let i = 0; i < questions.length; i++) {
            if (!questions[i].title.trim()) {
                alert(`题目 ${i + 1} 未填写标题`);
                event.preventDefault();
                return false;
            }
        }

        // 序列化（去掉内部 _id）
        const data = questions.map(function (q, idx) {
            return {
                question_type: q.question_type,
                title: q.title.trim(),
                description: q.description.trim(),
                options: (q.question_type === 'single_choice' || q.question_type === 'multi_choice')
                    ? (q.options || []).filter(function (o) { return o.trim(); })
                    : [],
                required: q.required,
                file_types: q.file_types || '',
                max_file_size_mb: q.max_file_size_mb ? parseInt(q.max_file_size_mb) : null,
                sort_order: idx,
            };
        });

        hiddenInput.value = JSON.stringify(data);
        return true;
    }

    // ── 工具 ───────────────────────────────────────────────────

    // L-10：原实现只转义 " < >，漏掉 & 与 '（自 XSS）。改复用公共 escAttr（全量转义）。
    function escapeAttr(str) {
        return (window.escAttr || function (s) {
            return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
                return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c];
            });
        })(str);
    }

    // ── 启动 ───────────────────────────────────────────────────
    if (container) {
        init();
    }
})();

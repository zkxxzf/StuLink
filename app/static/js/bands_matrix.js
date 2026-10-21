/* StuLink v1.19.50.64 2026-10-21
   划线分层矩阵（从 grades/bands.html 抽出，供「划线分层」页与「数据导入」页共用）。
   依赖 jQuery 与 DOM 元素 #bandApp / #bandTable；只读/可编辑由 data-can-edit 决定。 */
$(function(){
    var $app = $('#bandApp'), examId = $app.data('examId');
    // 2026-10-15 用户口径：无 grades.settings 的账号（任课教师 / 班主任）只读查看 ——
    // 表格渲染为文本（不出输入框 / 勾选框 / 删除按钮），编辑控件已由服务端隐藏
    var canEdit = ('' + $app.data('canEdit')) === '1';
    var dirs = $app.data('dirs') || [''];
    var dirSubjects = $app.data('dirSubjects') || {};
    var totalCount = parseInt($app.data('totalCount') || 0, 10) || 0;
    var layers = [];
    var hideEmpty = true;    // 全空列默认收起（用户口径 2026-10-13）
    var hiddenCols = [];     // 本次渲染实际收起的列 key（'方向|科目'）
    // 2026-10-15 组合线：{'方向|科目': {层归一名: {组合: 分数}}} —— 由 load() 从 matrix 回填，
    // 有组合线的格子按组合分行渲染输入框（每组合一行，可改、随保存写入）
    var selNotes = {};
    // 组合元数据：{'物化生': {direction, subjects, band_subjects, count}}（后端 sel_meta 传入）
    var selMeta = $app.data('selMeta') || {};
    // 组合级科目白名单：由后端注入（单一来源 bands.COMBO_SUBJECTS，审计 S2-1 不再前端复制）。
    // 注入缺失时退化为空 → 只认后端 band_subjects，不会误把语数外/首选科当组合级
    var COMBO_SUBS = $app.data('comboSubjects') || [];
    // 科目 → 本场考该科的组合（**含还没划线的组合**）：格子固定列出它们，
    // 没划过线也能直接填（用户口径「我们根本无法添加标签呀」）
    var subSels = {};
    Object.keys(selMeta).forEach(function(sel){
        var m = selMeta[sel] || {};
        var subs = (m.band_subjects && m.band_subjects.length)
            ? m.band_subjects
            : (m.subjects || []).filter(function(s){ return COMBO_SUBS.indexOf(s) >= 0; });
        subs.forEach(function(s){
            var arr = subSels[s] || (subSels[s] = []);
            if (arr.indexOf(sel) < 0) arr.push(sel);
        });
    });
    /* 该（方向 × 科目）格子里要列出哪些组合：
       - 总分列 → 该方向的全部组合（总分一律按组合划）
       - 再选四科 → 该方向中考了这科的组合（未划线也列出来，填了就新建）
       - 语数外 / 首选科 → 不列（按方向划，走普通方向输入格） */
    function selsOf(d, s){
        var all = (s === '总分') ? Object.keys(selMeta) : (subSels[s] || []);
        return all.filter(function(sel){
            return ((selMeta[sel] || {}).direction || '') === (d || '');
        });
    }
    // 2026-10-15 列级开关（用户口径「在该列顶部加个是否按照组合分析，勾选了就显示这几个组合，
    // 不勾选就不显示」）：{'物理|化学': true} —— 该列按组合分行填；未勾 = 该科按方向一个标准。
    // 默认值在 load() 里按「该列是否已有组合线」推断（有则勾选）。
    var comboCols = {};
    function comboOn(k){ return !!comboCols[k]; }
    /* 科目表头：可组合的列（总分 / 化 / 生 / 政 / 地）带「按组合」勾选框 */
    function subHeadTh(d, s){
        var k = d + '|' + s;
        var sels = selsOf(d, s);
        var $w = $('<div class="d-flex align-items-center justify-content-center gap-1">');
        if (canEdit && scope() !== 'all' && sels.length){
            $w.append($('<input type="checkbox" class="form-check-input q-combo-chk m-0">')
                .attr('data-key', k).prop('checked', comboOn(k))
                .attr('title', '按组合划线：勾选后本列按选科组合分行填（' + sels.join(' / ')
                      + '）；不勾＝本列按方向一个标准'));
        }
        $w.append($('<span>').text(s));
        return $w;
    }
    function normName(n){ return ((n || '') + '').trim().replace(/线$/, ''); }
    /* 只读视图取值文本：空值出淡「—」，有值出数字（与输入框同内容） */
    function readonlyText(v){
        if (v == null || v === '') return $('<span class="text-muted">—</span>');
        return $('<span>').text(v);
    }
    /* 整表「划线维度」下拉已取消（2026-10-15 用户口径＝**列级「按组合」开关**，见 comboCols）。
       selKey() 恒为 ''（＝按方向）；后端 matrix 的 ?selection= 分支仅留给脚本/测试直调。 */
    function selKey(){ return ''; }
    function isCombo(){ return !!selKey(); }
    function comboMeta(){ return selMeta[selKey()] || {}; }
    function viewDirs(){ return isCombo() ? [selKey()] : dirs; }

    function mode(){ return $('#qMode').val(); }
    function scope(){ return $('#qScope').val(); }
    // 单位（用于表头与校验提示）：分数 / 比例% / 名次
    function unit(){ return mode() === 'ratio' ? '比例%' : (mode() === 'rank' ? '名次' : '分数'); }
    function colsOf(d){
        var subs = (isCombo() ? (comboMeta().subjects || []) : (dirSubjects[d] || [])).slice();
        // 2026-10-21：数据导入页内嵌（simple）时没有「含总分」开关 → 默认包含总分
        if (!$('#qTotal').length || $('#qTotal').is(':checked')) subs.unshift('总分');
        return subs;
    }
    function colKeys(){
        var keys = [];
        if (scope() === 'all') return ['*'];
        viewDirs().forEach(function(d){ colsOf(d).forEach(function(s){ keys.push(d + '|' + s); }); });
        return keys;
    }
    function colLabel(k){
        if (k === '*') return '（全部学科）';
        return k.replace('|', isCombo() ? '·' : '类·');
    }
    function dirLabel(d){
        return isCombo()
            ? (d + '（' + (comboMeta().direction || '') + '类）')
            : (d ? (d + '类') : '全体（未分科）');
    }

    /* 空列折叠（用户口径 2026-10-13）：
       「历史类·物理」这类跨文理错位的列，逐格填值永远是空的，却把表撑得很散。
       默认把「所有层都没值」的列收起来（工具栏按钮可展开）；
       若整场考试一列都没填（初次录入），则全部展开 —— 否则用户无处可填。
       注意：收起的列只是不渲染，后端 batch 对「整列未填」是跳过、不动已有划线（bands.py）。 */
    function colHasData(k){
        // 2026-10-15 组合级划线：只有组合线的列也算"有数据"，
        // 否则「化学」这类只按组合划线、方向格全空的列会被当空列收起
        var notes = selNotes[k];
        if (notes && Object.keys(notes).length) return true;
        // 勾了「按组合」的列常显（还没划线时也要能直接填）
        if (scope() !== 'all' && k && k !== '*'){
            var p = k.split('|');
            if (!isCombo() && p.length > 1 && comboOn(k) && selsOf(p[0], p[1]).length){
                return true;
            }
        }
        return layers.some(function(l){
            var v = (scope() === 'all') ? l.value : ((l.values || {})[k]);
            return v !== '' && v != null;
        });
    }
    function emptyColKeys(){
        if (scope() === 'all') return [];
        var keys = colKeys();
        var empty = keys.filter(function(k){ return !colHasData(k); });
        return empty.length === keys.length ? [] : empty;   // 全空 = 初次录入，不收起
    }
    // 实际收起的列：只有"收起"状态才真的隐藏（按钮统计用的是 emptyColKeys —— 与开关无关，
    // 否则展开后空列数为 0，按钮会变成「无空列」禁用，再也收不回去）
    function computeHiddenCols(){
        return hideEmpty ? emptyColKeys() : [];
    }
    function colShown(k){ return hiddenCols.indexOf(k) < 0; }
    function updateEmptyBtn(){
        var n = emptyColKeys().length, $b = $('#qEmpty');   // 用"空列总数"，与当前展开/收起无关
        if (!n){
            $b.prop('disabled', true)
              .attr('title', '所有科目列都有划线，无需收起')
              .html('<i class="bi bi-eye-slash"></i> 无空列');
            return;
        }
        $b.prop('disabled', false);
        if (hideEmpty){
            $b.attr('title', '展开这 ' + n + ' 个全空的科目列（如确需给它们划线）')
              .html('<i class="bi bi-eye"></i> 显示空列（' + n + '）');
        } else {
            $b.attr('title', '把全空的科目列收起来')
              .html('<i class="bi bi-eye-slash"></i> 隐藏空列（' + n + '）');
        }
    }

    function buildHead(){
        var $th = $('#bandTable thead').empty();
        // 2026-10-13：未分科（只有一组）时只出一行表头 —— 分组标题行在只有一组时
        // 没有信息量，却把表头撑成上下两条空色带（用户截图里就是这一块）。
        var single = (scope() === 'all') || isCombo()
                     || (dirs.length === 1 && !dirs[0]);
        if (single){
            var d0 = viewDirs()[0] || '';
            var r = $('<tr>');
            r.append('<th class="col-layer">层名</th>');
            if (scope() === 'all'){
                r.append($('<th class="col-sub">').text(unit() + '（全部学科共用）'));
            } else {
                colsOf(d0).filter(function(s){ return colShown(d0 + '|' + s); })
                          .forEach(function(s){
                              r.append($('<th class="col-sub">').append(subHeadTh(d0, s)));
                          });
            }
            if (canEdit){ r.append('<th class="col-op"></th>'); }   // 只读无删除列
            $th.append(r);
            return;
        }
        var r1 = $('<tr>'), r2 = $('<tr>');
        r1.append('<th rowspan="2" class="col-layer">层名</th>');
        viewDirs().forEach(function(d){
            // 收起的空列不参与表头；整组都被收起时连分组标题一起省掉
            var subs = colsOf(d).filter(function(s){ return colShown(d + '|' + s); });
            if (!subs.length) return;
            // 2026-10-13：分组标题居中 —— 原先默认左对齐，「历史类」贴在跨 7 列分组
            // 最左边、正压在「总分」上方，看起来像 colspan 错位（"表头两行"的观感来源）
            r1.append($('<th class="text-center head-group">').attr('colspan', subs.length).text(dirLabel(d)));
            subs.forEach(function(s){
                r2.append($('<th class="text-center col-sub">').append(subHeadTh(d, s)));
            });
        });
        if (canEdit){ r1.append('<th rowspan="2" class="col-op"></th>'); }
        $th.append(r1).append(r2);
    }
    function render(){
        hiddenCols = computeHiddenCols();   // 先定下本次要收起的空列（表头与表体共用）
        buildHead();
        var $tb = $('#bandTable tbody').empty();
        // 2026-10-16 第八轮审计（U-16）：本场还没划线且未套模板时，layers 为空 →
        // 表体只有表头的空白网格，用户不知道该点「套用层名」还是「新增层」。补空态行。
        if (!layers.length){
            // 2026-10-21：数据导入页内嵌只读视图没有编辑按钮 → 空态文案不能提示「点上方…」
            var emptyTxt = $('#qAdd').length
                ? '本场还没有划线层。点上方「套用层名」选一套模板，或点「新增层」手工添加。'
                : '本场还没有划线。';
            $tb.append($('<tr>').append($('<td>')
                .attr('colspan', 99)
                .addClass('text-center text-muted py-4')
                .text(emptyTxt)));
            updateEmptyBtn();
            return;
        }
        layers.forEach(function(l){
            var tr = $('<tr>');
            // 2026-10-13：不在当前模板层名内的「模板外」行加标记 —— 这类行通常是
            // 模板没选对时由已保存划线追加出来的，混在模板层里看不出区别
            // （用户口径："模板选的正确与否，背景色和其他的也要跟着对"）
            if (l.extra){ tr.addClass('q-extra'); }
            // 模板层带比例定义时，层名下方展示「比例 x% · 约 N 人」
            // （N = 参考人数 × 比例，估算值，帮助先对上模板再填数值）
            var $name = $('<div>');
            if (canEdit){
                $name.append($('<input class="form-control form-control-sm q-name" style="min-width:120px">')
                    .val(l.name).attr('data-ratio', (l.ratio == null ? '' : l.ratio)));
            } else {
                $name.append($('<span class="fw-semibold">').text(l.name));   // 只读：层名出文本
            }
            if (l.extra){
                $name.append($('<div class="small">').addClass('text-warning')
                    .text('不在当前模板内'));
            }
            if (l.ratio !== '' && l.ratio != null && !isNaN(parseFloat(l.ratio))){
                var r = parseFloat(l.ratio);
                // 组合维度下按该组合人数估算（否则「约 N 人」会拿全年级人数套，误导填比例）
                var base = isCombo() ? (comboMeta().count || 0) : totalCount;
                var est = base ? Math.round(base * r / 100) : 0;
                $name.append($('<div class="small text-muted">').text(
                    '模板定义：比例 ' + r + '%' + (est ? ' · 约 ' + est + ' 人' : '')));
            }
            tr.append($('<td>').append($name));
            if (scope() === 'all'){
                if (canEdit){
                    tr.append($('<td>').append($('<input type="number" step="0.5" class="form-control form-control-sm q-value">').val(l.value == null ? '' : l.value)));
                } else {
                    tr.append($('<td>').append(readonlyText(l.value)));
                }
            } else {
                viewDirs().forEach(function(d){
                    colsOf(d).forEach(function(s){
                        var k = d + '|' + s;
                        if (!colShown(k)) return;   // 收起的空列不渲染
                        var notes = (selNotes[k] || {})[normName(l.name)] || {};
                        var sels = selsOf(d, s);
                        // 组合线格（仅「按方向」维度）：
                        //  · 可编辑：勾了「按组合」的列 → 每组合一行「组合名 + 输入框」，
                        //    直接改、随「保存划线」写入该组合的线（2026-10-15 用户口径：
                        //    这些格子必须能直接输入，不能只给只读小字）；组合列表来自
                        //    「本场该科适用的组合」，**未划线也列出来**（填了就新建）；
                        //  · 只读：把该层已有的组合线逐条列出（组合名 + 线值文本）
                        var cols = canEdit
                            ? ((!isCombo() && comboOn(k)) ? sels : null)
                            : sels.filter(function(sel){ return notes[sel] != null; })
                                  .concat(Object.keys(notes).filter(function(sel){
                                      return sels.indexOf(sel) < 0; }));
                        if (cols && cols.length){
                            var $d = $('<td>');
                            cols.forEach(function(sel){
                                var v = notes[sel];
                                if (!canEdit){
                                    $d.append($('<div class="d-flex align-items-center gap-1 q-selrow">')
                                        .append($('<span class="q-sel-name flex-shrink-0">').text(sel))
                                        .append(readonlyText(v)));
                                    return;
                                }
                                var $r = $('<div class="d-flex align-items-center gap-1 q-selrow">');
                                $r.append($('<span class="q-sel-name flex-shrink-0">').text(sel));
                                // 组合格输入框的「框」：全表 input 的边框/底色被 theme.css
                                // （#bandTable td .form-control）统一去掉，组合格必须一眼看出
                                // 「这里能填」——2026-10-15 用户反馈「你这个框不是很明显」
                                // 后，边框/底色/宽度统一移进 theme.css 的 #bandTable 区块
                                // （原来内联 1px #adb5bd 太淡，且白底在深色下要靠覆盖）
                                $r.append($('<input type="number" step="0.5" class="form-control form-control-sm q-selcell">')
                                    .attr('data-key', k).attr('data-sel', sel)
                                    .attr('placeholder', v == null ? '未划' : '')
                                    .attr('title', sel + ' 的这条线（比例/名次按该组合自己的学生排名换算；'
                                          + '留空则该条线不变）')
                                    .val(v == null ? '' : v));
                                $d.append($r);
                            });
                            tr.append($d);
                            return;   // 该列由组合线承担（被取代的方向线已清）
                        }
                        if (!canEdit){
                            // 只读：方向线值出文本（未划线显 —）
                            tr.append($('<td>').append(readonlyText(
                                (l.values && l.values[k] != null) ? l.values[k] : '')));
                            return;
                        }
                        tr.append($('<td>').append($('<input type="number" step="0.5" class="form-control form-control-sm q-cell">')
                            .attr('data-key', k)
                            .val((l.values && l.values[k] != null) ? l.values[k] : '')));
                    });
                });
            }
            if (canEdit){
                // 2026-10-12 UI 审计 M6：纯图标按钮补 title/aria-label
                tr.append($('<td>').append($('<button type="button" class="btn btn-sm btn-outline-danger q-del" title="删除该层" aria-label="删除该层"><i class="bi bi-x"></i></button>')));
            }
            $tb.append(tr);
        });
        updateEmptyBtn();
    }
    function read(){
        var out = [];
        $('#bandTable tbody tr').each(function(i){
            var $r = $(this), o = {};
            o.seq = i + 1;   // 行序即层序，不再单独维护序号输入列
            o.name = $r.find('.q-name').val();
            // 模板比例定义随行保存（重新 render 后提示不丢）
            var ratio = $r.find('.q-name').attr('data-ratio');
            if (ratio !== '' && ratio != null) o.ratio = ratio;
            if ($r.hasClass('q-extra')) o.extra = true;
            if (scope() === 'all'){
                o.value = $r.find('.q-value').val();
            } else {
                o.values = {};
                $r.find('.q-cell').each(function(){ o.values[$(this).data('key')] = $(this).val(); });
                // 组合线格：{'方向|科目': {'组合': 值}}（按方向维度下格内逐组合填的值）
                o.sel_values = {};
                $r.find('.q-selcell').each(function(){
                    var kk = $(this).data('key'), sel = $(this).data('sel');
                    (o.sel_values[kk] || (o.sel_values[kk] = {}))[sel] = $(this).val();
                });
            }
            out.push(o);
        });
        return out;
    }
    function _selHas(m){
        return Object.keys(m || {}).some(function(kk){
            var by = m[kk] || {};
            return Object.keys(by).some(function(s){ return by[s] !== '' && by[s] != null; });
        });
    }
    function hasValue(){
        return read().some(function(x){
            if (scope() === 'all') return x.value !== '' && x.value != null;
            return Object.keys(x.values || {}).some(function(k){ return x.values[k] !== ''; })
                || _selHas(x.sel_values);
        });
    }
    /* 逐「列 / 组合」检查取值区间与层间顺序（自上而下：分数递减、比例/名次递增） */
    function checkSeq(label, payload, getVal, errs){
        var prev = null, prevName = '';
        payload.forEach(function(l){
            var raw = getVal(l);
            if (raw === '' || raw == null) return;
            var v = parseFloat(raw);
            if (isNaN(v)){ errs.push(label + '「' + l.name + '」不是数字'); return; }
            if (v < 0){ errs.push(label + '「' + l.name + '」不能为负数'); }
            if (mode() === 'ratio' && v > 100){ errs.push(label + '「' + l.name + '」比例需在 0-100 之间'); }
            if (mode() === 'rank' && v < 1){ errs.push(label + '「' + l.name + '」名次需 ≥ 1'); }
            if (prev !== null){
                if (mode() === 'score'){
                    if (v > prev){
                        errs.push(label + '「' + l.name + '」下界 ' + v
                                  + ' 高于上一层「' + prevName + '」' + prev);
                    }
                } else {
                    // 比例 / 名次都是「前 n」口径，自上而下应**递增**
                    //（前20% → 前60%、前50名 → 前200名）；末层常填 0 表示兜底，跳过比较。
                    // 真正的递减校验由后端换算成分数线后再做（同分可能落在同一界）。
                    if (v > 0 && prev > 0 && v < prev){
                        errs.push(label + '「' + l.name + '」' + unit() + ' ' + v
                                  + ' 低于上一层「' + prevName + '」' + prev
                                  + '（' + unit() + '应自上而下递增）');
                    }
                }
            }
            prev = v; prevName = l.name;
        });
    }

    /* 提交前校验：逐列（含格内各组合）检查取值与顺序，避免提交后才报失败 */
    function validate(payload){
        var errs = [];
        payload.forEach(function(x){
            if (!x.name){ errs.push('有层的「层名」为空'); }
        });
        colKeys().forEach(function(k){
            checkSeq(colLabel(k), payload, function(l){
                return (scope() === 'all') ? l.value : (l.values || {})[k];
            }, errs);
        });
        // 组合线格：按 (列, 组合) 逐条校验
        var selKeys = {};
        payload.forEach(function(l){
            var sv = l.sel_values || {};
            Object.keys(sv).forEach(function(kk){
                Object.keys(sv[kk] || {}).forEach(function(s){ selKeys[kk + '\u0001' + s] = true; });
            });
        });
        Object.keys(selKeys).forEach(function(combo){
            var p = combo.split('\u0001');
            checkSeq(colLabel(p[0]) + '·' + p[1], payload, function(l){
                return ((l.sel_values || {})[p[0]] || {})[p[1]];
            }, errs);
        });
        return errs;
    }
    function setLayers(names){
        layers = names.map(function(n, i){ return {seq: i + 1, name: n, value: '', values: {}}; });
        render();
    }
    /* 已加载的划线回填到矩阵（保存后刷新用） */
    function load(){
        if (scope() === 'all'){ render(); return; }
        if (!colKeys().length){ render(); return; }
        // 一次请求取回整张矩阵：原逐列请求会让后端每列都重建一次 ExamData，列多时很慢
        // 2026-10-12 UI 审计 M4：失败原先只静默 render()（用户看到空表，不知是没划线
        // 还是加载失败）。改为给出明确错误提示，并保留"空表"与"加载失败"的区分。
        // selection 非空 = 「按组合」维度：只回该组合的线（可编辑）
        $.getJSON('/grades/api/bands/matrix',
                  {exam_id: examId, selection: selKey()}, function(res){
            var all = res.data || {};
            // 2026-10-13：回填不能只按层名**精确**匹配 —— 导入的线名与模板层名常差
            // 一个「线」字（库里「特控/本科/211」，模板层名「特控线/本科线」），
            // 差一个字整表就空白（用户实测命中）。改为「归一化名匹配」（去首尾空格 +
            // 去尾部「线」），库里多出来的层追加为新行，不再静默丢弃。
            var lib = {};                       // 归一化名 -> {name, seq}
            selNotes = {};                      // 组合线：{列: {层归一名: {组合: 分数}}}
            comboCols = {};                     // 列级开关默认值：已有组合线的列自动勾选
            Object.keys(all).forEach(function(k){
                (all[k] || []).forEach(function(b){
                    if (b.selection && !isCombo()){
                        // 组合线（「按方向」视图带回）：按「层归一名 → 组合 → 分数」收进
                        // selNotes（该列要列哪些组合由 selsOf() 从 band_subjects 推导）
                        var note = selNotes[k] || (selNotes[k] = {});
                        var bySel = note[normName(b.name)] || (note[normName(b.name)] = {});
                        bySel[b.selection] = b.lower_value;
                        comboCols[k] = true;     // 本列按组合划线（勾选框默认勾上）
                        return;
                    }
                    var key = normName(b.name);
                    if (key && !(key in lib)) lib[key] = {name: (b.name || '').trim(), seq: b.seq};
                });
            });
            // 2026-10-13：模板之外的层不再静默追加 —— 明确列出来提醒，
            // 通常说明模板没选对（先选对模板，已保存的线才能对号入座）
            var appended = [];
            Object.keys(lib).forEach(function(key){
                var has = layers.some(function(l){ return normName(l.name) === key; });
                if (!has){ appended.push(lib[key].name);
                    layers.push({seq: lib[key].seq, name: lib[key].name,
                                 value: '', values: {}, extra: true}); }
            });
            layers.sort(function(a, c){ return a.seq - c.seq; });
            colKeys().forEach(function(k){
                var byKey = {};
                (all[k] || []).forEach(function(b){
                    if (!b.selection) byKey[normName(b.name)] = b;
                });
                layers.forEach(function(l){
                    var b = byKey[normName(l.name)];
                    if (b && b.lower_value != null) l.values[k] = b.lower_value;
                });
            });
            // 2026-10-15：回填后让「下界方式」跟随库里的线 —— 模板常带「人数比例%」模式，
            // 而库里存的是换算后的分数，不改的话回填的 496.5 会被当比例校验、保存必然失败
            var modes = {};
            colKeys().forEach(function(k){
                (all[k] || []).forEach(function(b){
                    if (!b.selection && b.lower_mode){
                        modes[b.lower_mode] = (modes[b.lower_mode] || 0) + 1;
                    }
                });
            });
            var mkeys = Object.keys(modes);
            if (mkeys.length === 1 && $('#qMode').val() !== mkeys[0]){
                $('#qMode').val(mkeys[0]);   // 静默切换，不触发 change 的「清空确认」
            }
            $('#qMsg').removeClass('text-success text-danger');
            if (appended.length){
                $('#qMsg').addClass('text-warning')
                    .text('提示：已保存划线中有 ' + appended.length + ' 个层（'
                          + appended.join('、') + '）不在当前模板的层名内，已追加为新行；'
                          + '若与预期不符，请先在上方「分层模板」选择正确的模板再保存。');
            } else { $('#qMsg').text(''); }
            render();
        }).fail(function(x){
            var m = (x.responseJSON && x.responseJSON.message) || '已保存的划线加载失败';
            $('#qMsg').removeClass('text-success').addClass('text-danger')
                .text(m + '，请刷新页面重试（不影响本次填写）');
            render();   // 失败也刷新，避免整表空白
        });
    }

    $('#qMode').on('change', function(){
        // 分数与比例含义不同（如 120 分 vs 120%），切换时清空已填值，避免提交被拒
        if (hasValue() && !confirm('切换下界方式会清空已填数值（分数 / 比例 / 名次含义不同），继续？')){
            $('#qMode').val(mode() === 'ratio' ? 'score' : 'ratio');
            return;
        }
        layers = read();
        layers.forEach(function(l){ l.value = ''; l.values = {}; });
        render();
    });
    $('#qScope,#qTotal').on('change', function(){ layers = read(); render(); });
    // 2026-10-15 列级「按组合」开关（科目列头的小方框）：
    // 勾选 → 该列按选科组合分行填；取消 → 该科按方向一个标准（单格）。
    // 用户口径（2026-10-15）：「本列只有**一个**组合时（如历史类只有史地政），两套口径等价 ——
    // 切换直接搬值，不清空、不弹确认」；只有多组合（1 条方向值 ↔ N 条组合线无法自动对应）
    // 才提示确认并清空（确认框里列出已填的组合，避免"数据去哪了"）。
    $('#bandTable').on('change', '.q-combo-chk', function(){
        var $chk = $(this);
        var k = $chk.data('key'), on = $chk.is(':checked');
        layers = read();
        var p = k.split('|');
        var sels = selsOf(p[0], p[1]);
        var one = (sels.length === 1) ? sels[0] : '';   // 唯一组合名（两套等价，可搬值）
        var had = false;        // 该列是否已填值（多组合切换会清空，先确认）
        var filled = [];        // 已填的组合（确认框里列出来，避免"数据去哪了"）
        layers.forEach(function(l){
            var v = (l.values || {})[k];
            if (v !== '' && v != null) had = true;
            var by = (l.sel_values || {})[k];
            if (by){
                Object.keys(by).forEach(function(s){
                    if (by[s] !== '' && by[s] != null){
                        had = true;
                        if (filled.indexOf(s) < 0) filled.push(s);
                    }
                });
            }
        });
        if (had && !one && !confirm('本列有 ' + sels.length + ' 个组合，方向 1 条线 ↔ 组合 '
                + sels.length + ' 条线无法自动对应，切换「按组合」会清空本列已填数值'
                + (filled.length ? '（已填：' + filled.join('、') + '）' : '') + '，继续？')){
            $chk.prop('checked', !on);     // 还原勾选状态
            return;
        }
        var moved = false;
        layers.forEach(function(l){
            var v = (l.values || {})[k];
            var by = (l.sel_values || {})[k] || {};
            if (l.values) delete l.values[k];
            if (l.sel_values) delete l.sel_values[k];
            if (!one) return;              // 多组合：清掉（已确认）
            if (on){
                // 方向值 → 唯一组合（组合已有值时保留组合值，界面马上显示它）
                if (v !== '' && v != null && (by[one] == null || by[one] === '')){
                    (l.sel_values || (l.sel_values = {}))[k] = {};
                    l.sel_values[k][one] = v;
                    moved = true;
                }
            } else {
                // 唯一组合 → 方向值（组合该层空则保留原方向值）
                var cv = by[one];
                if (cv !== '' && cv != null){
                    (l.values || (l.values = {}))[k] = cv;
                    moved = true;
                }
            }
        });
        comboCols[k] = on;
        $('#qMsg').removeClass('text-success text-danger');
        if (moved && one){
            $('#qMsg').addClass('text-success').text(
                '本列只有「' + one + '」一个组合，两套口径等价 —— 值已直接带入，未清空');
        } else {
            $('#qMsg').text('');
        }
        render();
    });
    // （整表「划线维度」切换已取消：2026-10-15 三次口径后改为列级「按组合」开关，
    //   其切换逻辑见下方 .q-combo-chk 的 change 处理）
    // 空列展开 / 收起：点击前先把当前已填值收进 layers
    //（收起后那些格子不再渲染，但只要有值就不会被判定为空列，不会被收起）
    $('#qEmpty').on('click', function(){
        hideEmpty = !hideEmpty;
        // 只读视图里没有输入框，read() 会把已回填的数据清掉 —— 只读时不回读
        if (canEdit){ layers = read(); }
        render();
    });
    $('#qAdd').on('click', function(){
        layers = read();
        var maxSeq = layers.reduce(function(m, x){ return Math.max(m, +x.seq || 0); }, 0);
        layers.push({seq: maxSeq + 1, name: '', value: '', values: {}});
        render();
    });
    $('#bandTable').on('click', '.q-del', function(){
        layers = read();
        layers.splice($(this).closest('tr').index(), 1);
        render();
    });
    /* ---- 分层模板：套用层名 + 考试绑定（考试与模板绑定，分析按该模板的层算） ---- */
    var templates = [];
    function loadTemplates(cb){
        $.getJSON('/grades/api/band-templates', {exam_id: examId}, function(res){
            templates = (res.data || {}).templates || [];
            var sel = $('#qTpl').empty().append('<option value="">（不套用）</option>');
            templates.forEach(function(t){
                $('<option>').val(t.id).text(t.name + (t.is_builtin ? '（内置）' : '')).appendTo(sel);
            });
            var bound = (res.data || {}).bound_id;
            var suggested = (res.data || {}).suggested_id;
            if (bound){ sel.val(String(bound)); applyTemplate(true); showBound(bound); }
            else if (suggested){
                // 未绑定时自动匹配模板：优先按「本场已保存划线的层名」（归一化），
                // 其次按考试适用类型 —— 先把模板定准，已保存的线才能对号入座
                sel.val(String(suggested)); applyTemplate(true);
                var d = res.data || {};
                var why, tail;
                if (d.suggest_reason === 'lines'){
                    why = '已按本场已保存的划线自动匹配模板：';
                    tail = '（命中 ' + (d.suggest_hit || 0) + ' 条线'
                         + (d.suggest_extra ? '，模板另有 ' + d.suggest_extra + ' 层待填' : '')
                         + '；保存划线后正式绑定）';
                } else if (d.suggest_reason === 'default'){
                    why = '本场还没有可匹配的划线/类型，暂用内置模板：';
                    tail = '（可另行选择模板；保存划线后正式绑定）';
                } else {
                    why = '按考试类型（' + (d.exam_type || '') + '）自动匹配模板：';
                    tail = '（保存划线后正式绑定）';
                }
                $('#qTplBound').text(why + (tplById(suggested) || {}).name + tail);
            }
            else {
                setLayers(['优秀', '良好', '及格', '待提升']); showBound(null);
            }
            if (cb) cb();
        }).fail(function(){
            // 2026-10-12 UI 审计 M4：模板加载失败也明确提示（原为静默回落默认四层）
            $('#qMsg').removeClass('text-success').addClass('text-danger')
                .text('分层模板加载失败，已用默认四层占位，请刷新页面重试');
            setLayers(['优秀', '良好', '及格', '待提升']);
            if (cb) cb();
        });
    }
    function tplById(id){
        var hit = null;
        templates.forEach(function(t){ if (String(t.id) === String(id)) hit = t; });
        return hit;
    }
    function showBound(boundId){
        var t = tplById(boundId);
        $('#qTplBound').text(t ? ('本场已绑定模板：' + t.name + '（分析按此模板的层）')
                               : '本场未绑定模板（分析不出现分层指标）');
    }
    function applyTemplate(silent){
        var t = tplById($('#qTpl').val());
        if (!t){ if (!silent) alert('请先选择一个模板'); return; }
        if (!silent && hasValue()
            && !confirm('套用模板会按模板层名重建表格（已填数值将清空），继续？')) return;
        $('#qMode').val(t.lower_mode || 'score');
        var ratios = (t.layers || []).map(function(x){
            return (x.ratio === null || x.ratio === undefined) ? '' : x.ratio;
        });
        layers = (t.layers || []).map(function(x, i){
            return {seq: i + 1, name: x.name, value: '', values: {},
                    ratio: (x.ratio == null ? '' : x.ratio)};
        });
        // 比例/名次模板：把各层默认值预填进每个格子（分科时各科同值）
        if (t.lower_mode === 'ratio' || t.lower_mode === 'rank'){
            var keys = colKeys();
            layers.forEach(function(l, i){
                l.value = ratios[i];
                keys.forEach(function(k){ l.values[k] = ratios[i]; });
            });
        }
        render();
    }
    $('#qTplApply').on('click', function(){ applyTemplate(false); });
    $('#qTpl').on('change', function(){
        // 选模板即按模板层名重建表格（无需再点「套用层名」）；选「不套用」回到默认四层
        var id = $(this).val();
        if (!id){ setLayers(['优秀', '良好', '及格', '待提升']); showBound(null); return; }
        showBound(id);
        applyTemplate(true);
    });
    $('#qSave').on('click', function(){
        var payload = read();
        if (!payload.length){ alert('请先填层名或新增层'); return; }
        if (!hasValue()){ alert('请至少填写一个格子的下界值'); return; }
        var errs = validate(payload);
        if (errs.length){
            console.warn('[划线] 前端校验未通过，共 ' + errs.length + ' 处', errs);
            alert('以下 ' + errs.length + " 处需要修正：\n\n" + errs.slice(0, 8).join('\n')
                  + (errs.length > 8 ? '\n…（其余省略）' : ''));
            return;
        }
        var reqBody = {exam_id: examId, mode: mode(), scope: scope(),
                       include_total: $('#qTotal').is(':checked'), layers: payload,
                       selection: selKey(),                   // 非空=保存到该选科组合
                       template_id: $('#qTpl').val() || 0};   // 保存即绑定考试 ↔ 模板
        console.log('[划线] 提交保存', reqBody);
        if (!confirm('将按当前表格覆盖对应的划线，继续？')){ return; }
        // 2026-10-12 UI 审计 H3：保存是覆盖写操作，网络慢时重复点击会并发多次
        // 覆盖写（/grades/api/bands/batch）。提交期间禁用按钮 + 文案提示，
        // 与 exam_detail / student_query / report 的做法对齐。
        var $btn = $('#qSave').prop('disabled', true).text('保存中…');
        // 显式带 CSRF 头，不依赖 base.html 的 $.ajaxSetup（少一层失效风险）
        $.ajax({
            url: '/grades/api/bands/batch',
            type: 'POST',
            data: JSON.stringify(reqBody),
            contentType: 'application/json',
            headers: {'X-CSRFToken': (window.csrfToken || '')},
            success: function(res){
                console.log('[划线] 保存成功', res);
                $('#qMsg').removeClass('text-danger').addClass('text-success')
                    .text(res.message || '已保存');
                // 2026-10-12 UI 审计 M5：保存类反馈统一走非阻塞 toast
                if (window.stuToast) stuToast(res.message || '划线已保存');
                showBound($('#qTpl').val() || 0);
                load();
            },
            error: function(x){
                console.error('[划线] 保存失败 HTTP ' + x.status,
                              (x.responseText || '').slice(0, 300));
                var msg = x.responseJSON && x.responseJSON.message;
                if (!msg){
                    // 400/403 通常是安全校验未通过（返回的是 HTML，取不到 JSON 信息）
                    msg = (x.status === 400 || x.status === 403)
                        ? '页面已过期或安全校验未通过，请刷新页面（Ctrl+F5）后重试'
                        : ('服务器错误 ' + x.status);
                }
                alert('保存失败：' + msg);
            },
            complete: function(){
                $btn.prop('disabled', false).text('保存划线');
            }
        });
    });

    // 先取模板（本场已绑定则按其层名建表），再回填已保存的下界值
    loadTemplates(function(){ load(); });
});

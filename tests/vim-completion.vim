" Observe native completion; edits and navigation come only from the PTY.
set nomore
let s:case = json_decode(join(readfile(g:completion_case), "\n"))
let s:serial = 0

function! s:Capture() abort
    let s:serial += 1
    let l:state = {'serial': s:serial, 'mode': mode(1), 'line': line('.'),
                \ 'column': col('.'), 'lines': getline(1, '$'),
                \ 'pumvisible': pumvisible(), 'completion': complete_info()}
    call writefile([json_encode(l:state)], s:case.output . '.tmp')
    call rename(s:case.output . '.tmp', s:case.output)
endfunction

" Unlike Escape or <C-O>: probes, <Cmd> leaves INSERT and the popup active.
nnoremap <silent> <C-G> <Cmd>call <SID>Capture()<CR>
inoremap <silent> <C-G> <Cmd>call <SID>Capture()<CR>
execute 'edit ' . fnameescape(s:case.source)
set noswapfile

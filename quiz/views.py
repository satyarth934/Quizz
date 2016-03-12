import random

from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, render
from django.utils.decorators import method_decorator
from django.views.generic import DetailView, ListView, TemplateView, FormView

from .forms import QuestionForm, EssayForm
from .models import Quiz, Category, Progress, Sitting, Question
from essay.models import Essay_Question
from .track import TraceCalls


class QuizMarkerMixin(object):
    @method_decorator(login_required)
    def dispatch(self, *args, **kwargs):
        return super(QuizMarkerMixin, self).dispatch(*args, **kwargs)


class SittingFilterTitleMixin(object):
    def get_queryset(self):
        queryset = super(SittingFilterTitleMixin, self).get_queryset()
        quiz_filter = self.request.GET.get('quiz_filter')
        if quiz_filter:
            queryset = queryset.filter(quiz__title__icontains=quiz_filter)

        return queryset


class QuizListView(ListView):
    model = Quiz

    def get_queryset(self):
        queryset = super(QuizListView, self).get_queryset()
        return queryset.filter(draft=False)


class QuizDetailView(DetailView):
    model = Quiz
    slug_field = 'url'

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()

        if self.object.draft and not request.user.has_perm('quiz.change_quiz'):
            raise PermissionDenied

        context = self.get_context_data(object=self.object)
        return self.render_to_response(context)


class CategoriesListView(ListView):
    template_name='CategoryList.html'
    model = Category


class ViewQuizListByCategory(ListView):
    model = Quiz
    template_name = 'QuizListTheme.html'

    def dispatch(self, request, *args, **kwargs):
        self.category = get_object_or_404(Category, category=self.kwargs['category_name'])

        return super(ViewQuizListByCategory, self).\
            dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super(ViewQuizListByCategory, self)\
            .get_context_data(**kwargs)

        context['category'] = self.category
        return context

    def get_queryset(self):
        queryset = super(ViewQuizListByCategory, self).get_queryset()
        return queryset.filter(category=self.category, draft=False)


class QuizUserProgressView(TemplateView):
    template_name = 'ProgressTheme.html'

    @method_decorator(login_required)
    def dispatch(self, request, *args, **kwargs):
        return super(QuizUserProgressView, self)\
            .dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super(QuizUserProgressView, self).get_context_data(**kwargs)
        progress, c = Progress.objects.get_or_create(user=self.request.user)
        context['exams'] = progress.show_exams()
        return context


class QuizMarkingList(QuizMarkerMixin, SittingFilterTitleMixin, ListView):
    model = Sitting

    def get_queryset(self):
        queryset = super(QuizMarkingList, self).get_queryset()\
                                               .filter(complete=True)

        user_filter = self.request.GET.get('user_filter')
        if user_filter:
            queryset = queryset.filter(user__username__icontains=user_filter)

        return queryset


class QuizMarkingDetail(QuizMarkerMixin, DetailView):
    model = Sitting
    template_name='quiz/SittingTheme.html'

    def post(self, request, *args, **kwargs):
        sitting = self.get_object()

        q_to_toggle = request.POST.get('qid', None)
        if q_to_toggle:
            q = Question.objects.get_subclass(id=int(q_to_toggle))
            if int(q_to_toggle) in sitting.get_incorrect_questions:
                sitting.remove_incorrect_question(q)
            else:
                sitting.add_incorrect_question(q)

        return self.get(request)

    def get_context_data(self, **kwargs):
        context = super(QuizMarkingDetail, self).get_context_data(**kwargs)
        context['questions'] =\
            context['sitting'].get_questions(with_answers=True)
        return context


class QuizTake(FormView):
    form_class = QuestionForm
    template_name = 'QuestionTheme.html'

    
    def dispatch(self, request, *args, **kwargs):
        self.quiz = get_object_or_404(Quiz, url=self.kwargs['quiz_name'])
        if self.quiz.draft and not request.user.has_perm('quiz.change_quiz'):
            raise PermissionDenied

        self.logged_in_user = self.request.user.is_authenticated()

        if self.logged_in_user:
            self.sitting = Sitting.objects.user_sitting(request.user,
                                                        self.quiz)
        else:
            raise PermissionDenied

        if self.sitting is False:
            return render(request, 'single_complete.html')

        return super(QuizTake, self).dispatch(request, *args, **kwargs)
        
    
    def get_form(self, form_class):
        if self.logged_in_user:
            self.question = self.sitting.get_first_question()
            self.progress = self.sitting.progress()
        else:
            raise PermissionDenied

        if self.question.__class__ is Essay_Question:
            form_class = EssayForm

        return form_class(**self.get_form_kwargs())
    
    def get_form_kwargs(self):
        kwargs = super(QuizTake, self).get_form_kwargs()

        return dict(kwargs, question=self.question)
    
    def form_valid(self, form):
        if self.logged_in_user:
            self.form_valid_user(form)
            if self.sitting.get_first_question() is False:
                return self.final_result_user()
        else:
            raise PermissionDenied
            
        self.request.POST = {}

        return super(QuizTake, self).get(self, self.request)
    
    def get_context_data(self, **kwargs):
        context = super(QuizTake, self).get_context_data(**kwargs)
        context['question'] = self.question
        context['quiz'] = self.quiz
        if hasattr(self, 'previous'):
            context['previous'] = self.previous
        if hasattr(self, 'progress'):
            context['progress'] = self.progress
        return context
    
    def form_valid_user(self, form):
        progress, c = Progress.objects.get_or_create(user=self.request.user)
        guess = form.cleaned_data['answers']
        is_correct = self.question.check_if_correct(guess)

        if is_correct is True:
            self.sitting.add_to_score(1)
        else:
            self.sitting.add_incorrect_question(self.question)

        if self.quiz.answers_at_end is not True:
            self.previous = {'previous_answer': guess,
                             'previous_outcome': is_correct,
                             'previous_question': self.question,
                             'answers': self.question.get_answers(),
                             'question_type': {self.question
                                               .__class__.__name__: True}}
        else:
            self.previous = {}

        self.sitting.add_user_answer(self.question, guess)
        self.sitting.remove_first_question()
    
    def final_result_user(self):
        results = {
            'quiz': self.quiz,
            'score': self.sitting.get_current_score,
            'max_score': self.sitting.get_max_score,
            'percent': self.sitting.get_percent_correct,
            'sitting': self.sitting,
            'previous': self.previous,
        }

        self.sitting.mark_quiz_complete()

        results['questions'] =\
              self.sitting.get_questions(with_answers=True)
        results['incorrect_questions'] =\
              self.sitting.get_incorrect_questions

        if self.quiz.exam_paper is False:
            self.sitting.delete()

        return render(self.request, 'ResultTheme.html', results)
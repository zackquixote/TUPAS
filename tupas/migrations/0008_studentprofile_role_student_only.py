from django.db import migrations, models


def teacher_to_student(apps, schema_editor):
    """TEACHER was removed. Those accounts become STUDENT."""
    Profile = apps.get_model('tupas', 'StudentProfile')
    Profile.objects.filter(role='TEACHER').update(role='STUDENT')


def student_to_teacher(apps, schema_editor):
    Profile = apps.get_model('tupas', 'StudentProfile')
    Profile.objects.filter(role='TEACHER').update(role='STUDENT')  # no-op reverse


class Migration(migrations.Migration):

    dependencies = [
        ('tupas', '0007_alter_activitylog_options_activitylog_request_id_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='studentprofile',
            name='role',
            field=models.CharField(
                choices=[('STUDENT', 'Student')],
                default='STUDENT',
                help_text='ADMIN is any User with is_staff or is_superuser. TEACHER was removed.',
                max_length=20,
            ),
        ),
        migrations.RunPython(teacher_to_student, student_to_teacher),
    ]
